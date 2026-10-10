# -*- coding: utf-8 -*-
"""
四维度统一评分（跑在 envs\\qwen3tts）。

 1. 音色相似度 : 只统计有声帧的 CAM++ 声纹，锚点用源音频 400-412s 的另一段参考音色台词
                 （独立于用来克隆的参考音频，避免自己考自己）
 2. 稳健谱平坦度: 只统计有声帧，不受各段静音比例影响（之前踩过这个坑）
 3. 停顿节奏    : 按段自适应门限统计停顿；同时给语速
 4. 内容准确率  : 读 transcribe.py 的转写 → 算 CER（抓漏字/重复/错读）

另外用项目自己的质检门（锚在 ref_tight）判定每段会不会被拦下重生成。

用法: python score.py [--engines ...]
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

EVAL = r'tts_eval'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quality import SegmentGate, SpeakerScorer  # noqa: E402

REF_TIGHT = r'reference_audio\ref_tight.wav'
FFMPEG = r'bin\ffmpeg.exe'
SRC_M4A = r'reference_audio\ref_source_audio.m4a'
ANCHOR_WAV = os.path.join(EVAL, 'tmp', '_anchor_400s.wav')
FRAME = 0.02


# ---------- 只统计有声帧的声纹 ----------
def fbank_vad(sc, wav, sr, rel=0.12, abs_floor=0.004):
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
    frames = np.lib.stride_tricks.as_strided(
        y, shape=(nf, fl), strides=(y.strides[0] * fs, y.strides[0])).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    th = max(abs_floor, rel * float(np.percentile(rms, 90)))
    sel = rms >= th
    if sel.sum() < 10:
        sel = rms >= np.percentile(rms, 50)
    if sel.sum() < 5:
        return None
    f = feat.numpy()[sel]
    return f - f.mean(axis=0, keepdims=True)


def embed_vad(sc, wav, sr):
    f = fbank_vad(sc, wav, sr)
    if f is None:
        return None
    out = sc.sess.run(None, {'input': f[np.newaxis].astype(np.float32)})
    e = out[0][0]
    return e / (np.linalg.norm(e) + 1e-9)


# ---------- 稳健谱平坦度 ----------
def flat_robust(wav, sr):
    import librosa
    w = np.asarray(wav, dtype=np.float32)
    if len(w) < 1024:
        return 0.0
    S = np.abs(librosa.stft(w, n_fft=1024, hop_length=512)) ** 2
    e = S.sum(axis=0)
    lvl = np.percentile(e, 90)
    sel = e >= max(np.percentile(e, 50), 0.12 * lvl)
    if sel.sum() < 4:
        sel = e >= np.percentile(e, 80)
    Sa = S[:, sel]
    gm = np.exp(np.mean(np.log(Sa + 1e-10), axis=0))
    am = np.mean(Sa, axis=0)
    return float(np.mean(gm / (am + 1e-10)))


# ---------- 停顿 ----------
def pause_stats(wav, sr, min_p=0.25):
    w = np.asarray(wav, dtype=np.float64)
    fr = int(sr * FRAME)
    n = len(w) // fr
    if n < 5:
        return dict(ratio=0.0, med=0.0, mean=0.0, n=0, speech=0.0, dur=len(w) / sr)
    rms = np.sqrt(np.mean(w[:fr * n].reshape(-1, fr) ** 2, axis=1))
    v = rms >= max(0.004, 0.12 * float(np.percentile(rms, 90)))
    ps, i = [], 0
    while i < n:
        if not v[i]:
            j = i
            while j < n and not v[j]:
                j += 1
            pl = (j - i) * FRAME
            if pl >= min_p:
                ps.append(pl)
            i = j
        else:
            i += 1
    d = len(w) / sr
    speech = v.sum() * FRAME
    a = np.array(ps) if ps else np.array([0.0])
    return dict(ratio=float((d - speech) / d) if d else 0.0,
                med=float(np.median(a)), mean=float(a.mean()), n=len(ps),
                speech=float(speech), dur=float(d))


def edit_dist(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engines', nargs='*',
                    default=['qwen3-0.6b', 'qwen3-1.7b', 'longcat-1b'])
    args = ap.parse_args()

    os.makedirs(os.path.dirname(ANCHOR_WAV), exist_ok=True)
    if not os.path.exists(ANCHOR_WAV):
        subprocess.run([FFMPEG, '-y', '-v', 'quiet', '-ss', '400', '-t', '12',
                        '-i', SRC_M4A, '-ar', '16000', '-ac', '1', ANCHOR_WAV],
                       check=True)
    sc = SpeakerScorer()
    aw, asr = sf.read(ANCHOR_WAV)
    if aw.ndim > 1:
        aw = aw.mean(axis=1)
    anchor = embed_vad(sc, aw, asr)
    print(f'独立锚点已就绪（源音频 400-412s）', flush=True)

    gate = SegmentGate(REF_TIGHT, scorer=sc)
    print(f'质检门: sim>={gate.sim_min} 窗>={gate.win_sim_min} '
          f'flat<={gate.flatness_max}\n', flush=True)

    reports = {}
    for eng in args.engines:
        d = os.path.join(EVAL, 'audio', eng)
        meta_p = os.path.join(d, 'meta.json')
        if not os.path.exists(meta_p):
            print(f'[skip] {eng}: 无 meta.json', flush=True)
            continue
        with open(meta_p, encoding='utf-8') as f:
            meta = json.load(f)
        tp = os.path.join(EVAL, 'reports', f'transcript_{eng}.json')
        trans = {}
        if os.path.exists(tp):
            with open(tp, encoding='utf-8') as f:
                trans = json.load(f)

        rows = []
        for r in meta['rows']:
            if not r.get('ok'):
                continue
            p = os.path.join(d, r['wav'])
            w, sr = sf.read(p)
            if w.ndim > 1:
                w = w.mean(axis=1)
            w = w.astype(np.float32)
            e = embed_vad(sc, w, sr)
            sim = float(np.dot(e, anchor)) if e is not None else None
            gok, gwhy, gm = gate.check(w, sr)
            ps = pause_stats(w, sr)
            tr = trans.get(str(r['idx']), {})
            cer = None
            if tr.get('ref'):
                cer = edit_dist(tr['ref'], tr.get('norm', '')) / max(len(tr['ref']), 1)
            rows.append(dict(idx=r['idx'], chars=r['chars'], dur=round(len(w) / sr, 2),
                             sim=None if sim is None else round(sim, 4),
                             flat=round(flat_robust(w, sr), 5),
                             gate_ok=bool(gok), gate_why=gwhy,
                             gate_sim=round(gm.get('sim', 0), 4),
                             pause_med=round(ps['med'], 3),
                             pause_ratio=round(ps['ratio'], 4),
                             speech_sec=round(ps['speech'], 2),
                             cer=None if cer is None else round(cer, 4),
                             hyp=tr.get('norm', '')[:80]))
        reports[eng] = dict(meta=dict(engine=meta.get('engine'),
                                      model_dir=meta.get('model_dir'),
                                      total_sec=meta.get('total_sec'),
                                      steps=meta.get('steps'),
                                      guidance=meta.get('guidance'),
                                      temperature=meta.get('temperature'),
                                      ref=meta.get('ref')),
                            rows=rows)
        # 汇总
        sims = np.array([r['sim'] for r in rows if r['sim'] is not None])
        fl = np.array([r['flat'] for r in rows])
        dur = np.array([r['dur'] for r in rows])
        sp = np.array([r['speech_sec'] for r in rows])
        ch = np.array([r['chars'] for r in rows])
        pa = np.array([r['pause_med'] for r in rows])
        cer = np.array([r['cer'] for r in rows if r['cer'] is not None])
        nbad = sum(1 for r in rows if not r['gate_ok'])
        rate = ch.sum() / max(sp.sum(), 1e-9) * 60
        reports[eng]['summary'] = dict(
            n=len(rows), sim_mean=float(sims.mean()) if len(sims) else None,
            sim_std=float(sims.std()) if len(sims) else None,
            sim_min=float(sims.min()) if len(sims) else None,
            flat_mean=float(fl.mean()), flat_max=float(fl.max()),
            pause_med_mean=float(pa.mean()), dur_mean=float(dur.mean()),
            chars_per_speech_min=float(rate),
            gate_bad=int(nbad), gate_bad_rate=float(nbad / max(len(rows), 1)),
            cer_mean=float(cer.mean()) if len(cer) else None,
            cer_max=float(cer.max()) if len(cer) else None,
            total_audio=float(dur.sum()), total_sec=meta.get('total_sec'),
            rtf=float(meta.get('total_sec', 0) / max(dur.sum(), 1e-9)))
        os.makedirs(os.path.join(EVAL, 'reports'), exist_ok=True)
        with open(os.path.join(EVAL, 'reports', f'score_{eng}.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(reports[eng], f, ensure_ascii=False, indent=1)
        print(f'[{eng}] {len(rows)} 段 -> reports/score_{eng}.json', flush=True)

    # ---------- 汇总表 ----------
    lines = ['# 三路引擎四维度对比', '',
             '同一批 12 个连续段落（第六章）、同一参考音频 ref_tight.wav、'
             '同一套采样参数（Qwen3）。', '',
             '| 引擎 | 相似度↑ | 标准差 | 最低 | 平坦度↓ | 平坦度max | '
             '停顿中位 | 语速(字/发声分) | CER↓ | 质检不合格 | RTF |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for eng, rep in reports.items():
        s = rep['summary']
        f = lambda v, n=3: '—' if v is None else f'{v:.{n}f}'
        lines.append(
            f"| **{eng}** | {f(s['sim_mean'],4)} | {f(s['sim_std'],4)} | "
            f"{f(s['sim_min'],4)} | {f(s['flat_mean'],5)} | {f(s['flat_max'],5)} | "
            f"{f(s['pause_med_mean'],2)}s | {f(s['chars_per_speech_min'],0)} | "
            f"{(str(round(s['cer_mean']*100,2))+'%') if s['cer_mean'] is not None else '—'} | "
            f"{s['gate_bad']}/{s['n']} ({s['gate_bad_rate']:.0%}) | {f(s['rtf'],2)} |")
    lines += ['', '说明：相似度越高越像参考音色；平坦度越低越干净；CER 越低内容越准。',
              'CER 需先跑 transcribe.py。']
    with open(os.path.join(EVAL, 'reports', 'summary.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n' + '\n'.join(lines), flush=True)
    print(f'\n-> reports/summary.md', flush=True)


if __name__ == '__main__':
    main()
