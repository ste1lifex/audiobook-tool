# -*- coding: utf-8 -*-
"""
段落音质审计：用 CAM++ 声纹模型量化"像不像参考音色"，同时量化杂音。

相似度：campplus.onnx 算 192 维声纹向量，与参考音频求余弦相似度。
        同一说话人通常 >0.6，明显漂移会掉到 0.4 以下。
杂音  ：谱平坦度(noise-likeness)、高频能量占比、削波率、静音占比、RMS。

用法:
  python voice_quality_audit.py --limit 20          # 先小样本验证
  python voice_quality_audit.py                     # 全量审计
  python voice_quality_audit.py --dir <chunk目录>
"""
import argparse
import csv
import glob
import os
import sys

import numpy as np
import onnxruntime as ort
import soundfile as sf

CAMPPLUS = r'CosyVoice\pretrained_models\CosyVoice2-0.5B\campplus.onnx'
REF_AUDIO = r'reference_audio\ref_clip_full.wav'
SR_FBANK = 16000


# ---------- 声纹 ----------
class SpeakerScorer:
    def __init__(self, model_path=CAMPPLUS):
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(model_path, so,
                                         providers=['CPUExecutionProvider'])
        import torch
        import torchaudio
        self.torch = torch
        self.torchaudio = torchaudio

    def fbank(self, wav, sr):
        t = self.torch
        w = t.from_numpy(wav.astype(np.float32)).unsqueeze(0)
        if sr != SR_FBANK:
            w = self.torchaudio.functional.resample(w, sr, SR_FBANK)
        feat = self.torchaudio.compliance.kaldi.fbank(
            w, num_mel_bins=80, dither=0.0, sample_frequency=SR_FBANK)
        feat = feat - feat.mean(dim=0, keepdim=True)
        return feat.numpy()

    def embed(self, wav, sr):
        feat = self.fbank(wav, sr)
        out = self.sess.run(None, {'input': feat[np.newaxis, :, :].astype(np.float32)})
        e = out[0][0]
        return e / (np.linalg.norm(e) + 1e-9)


# ---------- 杂音指标 ----------
def noise_features(wav, sr):
    w = wav.astype(np.float64)
    dur = len(w) / sr
    out = {'dur': dur}
    out['rms'] = float(np.sqrt(np.mean(w ** 2)))
    out['peak'] = float(np.max(np.abs(w)))
    out['clip_ratio'] = float((np.abs(w) > 0.995).mean())
    out['dc'] = float(np.mean(w))
    fr = int(sr * 0.02)
    n = len(w) // fr
    if n == 0:
        out.update(sil_ratio=1.0, flatness=0.0, hf_ratio=0.0, zcr=0.0, tail_rms=0.0)
        return out
    frames = w[:fr * n].reshape(-1, fr)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    out['sil_ratio'] = float((rms < 0.005).mean())
    out['tail_rms'] = float(rms[-min(50, n):].mean())
    out['zcr'] = float((np.diff(np.sign(w)) != 0).mean())

    # 谱平坦度：几何均值/算术均值，越接近 1 越像噪声
    import librosa
    S = np.abs(librosa.stft(w.astype(np.float32), n_fft=1024, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    # 只统计有语音的帧，避免把静音算进去
    e = S.sum(axis=0)
    act = e >= (np.percentile(e, 40) + 1e-12)
    if act.sum() > 0:
        Sa = S[:, act]
        gm = np.exp(np.mean(np.log(Sa + 1e-10), axis=0))
        am = np.mean(Sa, axis=0)
        out['flatness'] = float(np.mean(gm / (am + 1e-10)))
        hf = Sa[freqs >= 7000, :].sum()
        out['hf_ratio'] = float(hf / (Sa.sum() + 1e-10))
    else:
        out['flatness'] = 0.0
        out['hf_ratio'] = 0.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=r'output\海伯利安\_chunks')
    ap.add_argument('--ref', default=REF_AUDIO)
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 个（验证用）')
    ap.add_argument('--out', default=None, help='CSV 输出路径')
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, '*.wav')))
    if args.limit:
        files = files[:args.limit]
    if not files:
        print('没有找到 wav 文件')
        return 1
    out_csv = args.out or os.path.join(args.dir, '..', '_quality_audit.csv')
    print(f'审计 {len(files)} 个文件  (参考: {os.path.basename(args.ref)})', flush=True)

    sc = SpeakerScorer()
    rw, rsr = sf.read(args.ref)
    if rw.ndim > 1:
        rw = rw.mean(axis=1)
    ref_emb = sc.embed(rw, rsr)
    print(f'参考声纹已提取 (维度 {ref_emb.shape[0]})', flush=True)

    rows = []
    for i, f in enumerate(files):
        try:
            w, sr = sf.read(f)
        except Exception as e:
            print(f'  读取失败 {os.path.basename(f)}: {e}')
            continue
        if w.ndim > 1:
            w = w.mean(axis=1)
        if len(w) < sr * 0.5:
            continue
        emb = sc.embed(w, sr)
        sim = float(np.dot(emb, ref_emb))
        r = {'file': os.path.basename(f), 'sim': round(sim, 4)}
        r.update({k: round(v, 5) for k, v in noise_features(w, sr).items()})
        rows.append(r)
        if (i + 1) % 100 == 0:
            print(f'  {i+1}/{len(files)} ...', flush=True)

    # 写 CSV
    with open(out_csv, 'w', newline='', encoding='utf-8-sig') as fh:
        wtr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)
    print(f'\nCSV -> {out_csv}', flush=True)

    sims = np.array([r['sim'] for r in rows])
    print(f'\n===== 声纹相似度分布 (n={len(sims)}) =====')
    for p in (1, 5, 10, 25, 50, 75, 90, 99):
        print(f'  P{p:<3} = {np.percentile(sims, p):.4f}')
    print(f'  均值 {sims.mean():.4f}   标准差 {sims.std():.4f}')
    for th in (0.3, 0.4, 0.5, 0.6):
        print(f'  低于 {th}: {(sims < th).sum():5d} 段 ({(sims < th).mean():.1%})')

    print('\n===== 最不像参考音色的 15 段 =====')
    for r in sorted(rows, key=lambda x: x['sim'])[:15]:
        print(f"  sim={r['sim']:.3f} dur={r['dur']:6.1f}s flat={r['flatness']:.3f} "
              f"hf={r['hf_ratio']:.3f} sil={r['sil_ratio']:.2f} rms={r['rms']:.4f}  {r['file']}")

    print('\n===== 谱平坦度最高（最像噪声）的 15 段 =====')
    for r in sorted(rows, key=lambda x: -x['flatness'])[:15]:
        print(f"  flat={r['flatness']:.3f} hf={r['hf_ratio']:.3f} sim={r['sim']:.3f} "
              f"dur={r['dur']:6.1f}s rms={r['rms']:.4f}  {r['file']}")

    print('\n===== 时长异常 (>60s) =====')
    for r in sorted([r for r in rows if r['dur'] > 60], key=lambda x: -x['dur']):
        print(f"  dur={r['dur']:6.1f}s sim={r['sim']:.3f} sil={r['sil_ratio']:.2f} "
              f"tail_rms={r['tail_rms']:.4f}  {r['file']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
