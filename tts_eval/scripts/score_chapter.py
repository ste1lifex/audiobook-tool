# -*- coding: utf-8 -*-
"""
对整章（或半章）产出做四维度验证，并与已采纳的基准对比。

用法:
  python score_chapter.py --dir <chunk目录> [--transcribe]
  例: python score_chapter.py --dir output\\海伯利安\\_chunks_half
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf

EVAL = r'tts_eval'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quality import SegmentGate, SpeakerScorer, hf_tonality, spectral_flatness  # noqa: E402

REF = r'reference_audio\ref_tight.wav'
FFMPEG = r'bin\ffmpeg.exe'
SRC_M4A = r'reference_audio\ref_source_audio.m4a'
ANCHOR_WAV = os.path.join(EVAL, 'tmp', '_anchor_400s.wav')

# 已采纳基准（t ts_eval 12 段, Qwen3-0.6B, 同配方）
BASELINE = dict(sim=0.7779, sim_min=0.7514, hf=0.1241, hf_min=0.0954,
                pause_med=0.42, cer=0.1065)


def embed_vad(sc, wav, sr, rel=0.12, abs_floor=0.004):
    import torch
    import torchaudio
    w = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)).unsqueeze(0)
    if sr != 16000:
        w = torchaudio.functional.resample(w, sr, 16000)
    feat = sc.torchaudio.compliance.kaldi.fbank(
        w, num_mel_bins=80, dither=0.0, sample_frequency=16000)
    y = w.squeeze(0).numpy()
    fl, fs = 400, 160
    nf = 1 + (len(y) - fl) // fs if len(y) >= fl else 0
    if nf <= 0:
        return None
    fr = np.lib.stride_tricks.as_strided(
        y, shape=(nf, fl), strides=(y.strides[0] * fs, y.strides[0])).astype(np.float64)
    rms = np.sqrt(np.mean(fr ** 2, axis=1))
    th = max(abs_floor, rel * float(np.percentile(rms, 90)))
    sel = rms >= th
    if sel.sum() < 10:
        sel = rms >= np.percentile(rms, 50)
    if sel.sum() < 5:
        return None
    f = feat.numpy()[sel]
    f = f - f.mean(axis=0, keepdims=True)
    out = sc.sess.run(None, {'input': f[np.newaxis].astype(np.float32)})
    e = out[0][0]
    return e / (np.linalg.norm(e) + 1e-9)


def pause_stats(w, sr, min_p=0.25):
    w = np.asarray(w, dtype=np.float64)
    fr = int(sr * 0.02)
    n = len(w) // fr
    if n < 5:
        return 0.0, 0.0
    rms = np.sqrt(np.mean(w[:fr * n].reshape(-1, fr) ** 2, axis=1))
    v = rms >= max(0.004, 0.12 * float(np.percentile(rms, 90)))
    ps, i = [], 0
    while i < n:
        if not v[i]:
            j = i
            while j < n and not v[j]:
                j += 1
            pl = (j - i) * 0.02
            if pl >= min_p:
                ps.append(pl)
            i = j
        else:
            i += 1
    return (float(np.median(ps)) if ps else 0.0), float((n - v.sum()) * 0.02)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--limit', type=int, default=0, help='只评前 N 段（快速看趋势）')
    ap.add_argument('--transcribe', action='store_true', help='调 whisper 算 CER')
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, '*.wav')),
                   key=lambda p: int(re.search(r'_seg(\d+)\.wav$', p).group(1)))
    if args.limit:
        files = files[:args.limit]
    print(f'待评 {len(files)} 段 <- {args.dir}', flush=True)

    os.makedirs(os.path.dirname(ANCHOR_WAV), exist_ok=True)
    if not os.path.exists(ANCHOR_WAV):
        subprocess.run([FFMPEG, '-y', '-v', 'quiet', '-ss', '400', '-t', '12',
                        '-i', SRC_M4A, '-ar', '16000', '-ac', '1', ANCHOR_WAV], check=True)
    sc = SpeakerScorer()
    aw, asr = sf.read(ANCHOR_WAV)
    if aw.ndim > 1:
        aw = aw.mean(axis=1)
    anchor = embed_vad(sc, aw, asr)
    gate = SegmentGate(REF, scorer=sc)

    rows = []
    for k, p in enumerate(files, 1):
        w, sr = sf.read(p)
        if w.ndim > 1:
            w = w.mean(axis=1)
        w = w.astype(np.float32)
        e = embed_vad(sc, w, sr)
        sim = float(np.dot(e, anchor)) if e is not None else None
        gok, gwhy, gm = gate.check(w, sr)
        pmed, pratio = pause_stats(w, sr)
        rows.append(dict(seg=int(re.search(r'_seg(\d+)\.wav$', p).group(1)),
                         dur=round(len(w) / sr, 2),
                         sim=None if sim is None else round(sim, 4),
                         hf=round(hf_tonality(w, sr), 4),
                         flat=round(spectral_flatness(w, sr), 5),
                         gate_ok=bool(gok), gate_why=gwhy,
                         pause_med=round(pmed, 3), pause_ratio=round(pratio, 4)))
        if k % 50 == 0:
            s = np.array([r['sim'] for r in rows if r['sim'] is not None])
            print(f'  {k}/{len(files)}  当前 sim 均值 {s.mean():.4f} 最低 {s.min():.4f}',
                  flush=True)

    sims = np.array([r['sim'] for r in rows if r['sim'] is not None])
    hf = np.array([r['hf'] for r in rows])
    pmed = np.array([r['pause_med'] for r in rows])
    dur = np.array([r['dur'] for r in rows])
    bad = [r for r in rows if not r['gate_ok']]
    why = {}
    for r in bad:
        for part in r['gate_why'].split('; '):
            k = part.split('0.')[0].rstrip('0123456789.@s')
            why[k] = why.get(k, 0) + 1
    out = dict(dir=args.dir, n=len(rows),
               sim_mean=float(sims.mean()), sim_std=float(sims.std()),
               sim_min=float(sims.min()), sim_p1=float(np.percentile(sims, 1)),
               hf_mean=float(hf.mean()), hf_min=float(hf.min()),
               pause_med=float(pmed.mean()), total_min=float(dur.sum() / 60),
               bad=len(bad), bad_rate=len(bad) / max(len(rows), 1), why=why,
               rows=rows)
    os.makedirs(os.path.join(EVAL, 'reports'), exist_ok=True)
    tag = os.path.basename(os.path.normpath(args.dir))
    with open(os.path.join(EVAL, 'reports', f'chapter_{tag}.json'), 'w',
              encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f'\n===== {tag} 结果（n={len(rows)}, 共 {dur.sum()/60:.1f} 分钟）=====')
    print(f'  声纹相似度 : 均值 {sims.mean():.4f}  标准差 {sims.std():.4f}  '
          f'最低 {sims.min():.4f}  P1 {np.percentile(sims,1):.4f}')
    print(f'  电音指标   : 均值 {hf.mean():.4f}  最低 {hf.min():.4f}  (人声 0.18~0.23, 门限 0.05)')
    print(f'  段内停顿中位: {pmed.mean():.2f}s')
    print(f'  质检不合格 : {len(bad)}/{len(rows)} ({len(bad)/max(len(rows),1):.1%})  {why}')
    print(f'\n  与采纳基准(12段)对比:')
    print(f'    {"指标":<14}{"基准":>10}{"本次":>10}{"差":>10}')
    for nm, b, c in (('相似度均值', BASELINE['sim'], sims.mean()),
                     ('相似度最低', BASELINE['sim_min'], sims.min()),
                     ('电音指标', BASELINE['hf'], hf.mean()),
                     ('停顿中位s', BASELINE['pause_med'], pmed.mean())):
        print(f'    {nm:<14}{b:>10.4f}{c:>10.4f}{c-b:>+10.4f}')
    print(f'\n-> reports/chapter_{tag}.json', flush=True)

    if args.transcribe:
        print('\n提示: 跑 CER 请用 envs\\cosyvoice 执行:')
        print(f'  python {os.path.join(EVAL, "scripts", "transcribe_dir.py")} '
              f'--dir "{args.dir}"')


if __name__ == '__main__':
    main()
