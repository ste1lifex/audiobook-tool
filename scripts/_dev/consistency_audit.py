# -*- coding: utf-8 -*-
"""
段间一致性审计 —— 补上现有质检门缺失的维度。

现有指标全是"绝对"的：每段和参考音频/独立锚点比。
问题是：一段只要 >=0.62 就算过，即使它和**前后邻居**明显不像。
"有时候从参考音色的语音中偏离出去" 就是这种：绝对值没低到触发门限，
但听感上一段和另一段不是同一个人。

本脚本为每段算三个数：
  sim_anchor    到独立锚点（源音频里另一段台词）—— 现有的绝对口径
  sim_centroid  到本章其余段质心的相似度（留一法）—— 段间一致性
  sim_prev      到上一段的相似度 —— 局部跳变

用法:
  python consistency_audit.py --book 海伯利安的隕落 --ch 1 2 3
  python consistency_audit.py --book 海伯利安的隕落 --ch 20 --top 20
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
from quality import SpeakerScorer  # noqa: E402

REF = r'reference_audio\ref_tight.wav'
FFMPEG = r'bin\ffmpeg.exe'
SRC_M4A = r'reference_audio\ref_source_audio.m4a'
ANCHOR_WAV = os.path.join(EVAL, 'tmp', '_anchor_400s.wav')
BOOK_DIR = r'output\海伯利安的隕落'
CHUNK_DIR = os.path.join(BOOK_DIR, '_chunks')
SAFE_BOOK = '海伯利安的隕落'


def embed_vad(sc, wav, sr, rel=0.12, abs_floor=0.004):
    """只统计有声帧的声纹向量（避免静音比例污染）。"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', default=SAFE_BOOK)
    ap.add_argument('--ch', type=int, nargs='+', required=True)
    ap.add_argument('--top', type=int, default=12)
    ap.add_argument('--save', action='store_true')
    args = ap.parse_args()

    os.makedirs(os.path.dirname(ANCHOR_WAV), exist_ok=True)
    if not os.path.exists(ANCHOR_WAV):
        subprocess.run([FFMPEG, '-y', '-v', 'quiet', '-ss', '400', '-t', '12',
                        '-i', SRC_M4A, '-ar', '16000', '-ac', '1', ANCHOR_WAV], check=True)

    sc = SpeakerScorer()
    aw, asr = sf.read(ANCHOR_WAV)
    if aw.ndim > 1:
        aw = aw.mean(axis=1)
    anchor = embed_vad(sc, aw, asr)
    rw, rsr = sf.read(REF)
    if rw.ndim > 1:
        rw = rw.mean(axis=1)
    refe = embed_vad(sc, rw, rsr)

    all_rows = []
    for ch in args.ch:
        files = sorted(glob.glob(os.path.join(CHUNK_DIR, f'{args.book}_ch{ch:02d}_seg*.wav')),
                       key=lambda p: int(re.search(r'_seg(\d+)\.wav$', p).group(1)))
        if not files:
            print(f'!! ch{ch} 无文件')
            continue
        embs, metas = [], []
        for p in files:
            w, sr = sf.read(p)
            if w.ndim > 1:
                w = w.mean(axis=1)
            e = embed_vad(sc, w, sr)
            embs.append(e)
            metas.append(dict(seg=int(re.search(r'_seg(\d+)\.wav$', p).group(1)),
                              dur=round(len(w) / sr, 2)))
        idx = [i for i, e in enumerate(embs) if e is not None]
        E = np.stack([embs[i] for i in idx])
        M = [metas[i] for i in idx]
        S = E.sum(axis=0)
        n = len(idx)
        rows = []
        t_cursor = 0.0
        for k, i in enumerate(idx):
            e = E[k]
            loo = (S - e) / max(1, n - 1)                 # 留一质心，避免自己考自己
            loo /= (np.linalg.norm(loo) + 1e-9)
            rows.append(dict(
                ch=ch, seg=M[k]['seg'], dur=M[k]['dur'],
                t_in_ch=round(t_cursor, 1),
                sim_anchor=round(float(np.dot(e, anchor)), 4),
                sim_ref=round(float(np.dot(e, refe)), 4),
                sim_centroid=round(float(np.dot(e, loo)), 4),
                sim_prev=None if k == 0 else round(float(np.dot(e, E[k - 1])), 4),
            ))
            t_cursor += M[k]['dur']
        all_rows.extend(rows)
        sa = np.array([r['sim_anchor'] for r in rows])
        sc_ = np.array([r['sim_centroid'] for r in rows])
        print(f'--- ch{ch:02d}  n={len(rows)}  '
              f'anchor 均值 {sa.mean():.4f} 最低 {sa.min():.4f} | '
              f'一致性(到留一质心) 均值 {sc_.mean():.4f} 最低 {sc_.min():.4f}')

    if not all_rows:
        return
    sa = np.array([r['sim_anchor'] for r in all_rows])
    sct = np.array([r['sim_centroid'] for r in all_rows])
    print(f'\n{"="*76}\n全章合计 n={len(all_rows)}')
    print(f'  sim_anchor   : 均值 {sa.mean():.4f}  sd {sa.std():.4f} '
          f'最低 {sa.min():.4f}  P1 {np.percentile(sa,1):.4f}  P5 {np.percentile(sa,5):.4f}')
    print(f'  sim_centroid : 均值 {sct.mean():.4f}  sd {sct.std():.4f} '
          f'最低 {sct.min():.4f}  P1 {np.percentile(sct,1):.4f}  P5 {np.percentile(sct,5):.4f}')

    # 现有门限（0.62）判定 vs 一致性判定
    gate_bad = [r for r in all_rows if r['sim_anchor'] < 0.62]
    med = float(np.median(sct))
    mad = float(np.median(np.abs(sct - med))) or 1e-6
    outl = [r for r in all_rows if r['sim_centroid'] < med - 3 * mad]
    both = [r for r in outl if r['sim_anchor'] < 0.62]
    print(f'\n  现有门(anchor<0.62) 判不合格 : {len(gate_bad)} 段 ({len(gate_bad)/len(all_rows):.2%})')
    print(f'  一致性离群(质心<中位-3MAD)   : {len(outl)} 段 ({len(outl)/len(all_rows):.2%})')
    print(f'  两者重合                     : {len(both)} 段')
    print(f'  >>> 一致性多抓出来的（门放过的）: {len(outl)-len(both)} 段 '
          f'({(len(outl)-len(both))/len(all_rows):.2%})  ← 就是"听着偏离"的嫌疑段')
    print(f'  一致性判定参考: 中位 {med:.4f}  3MAD={3*mad:.4f}  阈值 {med-3*mad:.4f}')

    print(f'\n  最不像邻居的 {args.top} 段（按一致性升序）:')
    print(f'    {"章":>4}{"段":>6}{"章内秒":>9}{"时长":>7}{"锚点":>9}{"一致性":>9}{"上一段":>9}')
    for r in sorted(all_rows, key=lambda x: x['sim_centroid'])[:args.top]:
        print(f"    ch{r['ch']:<2d}{r['seg']:>6}{r['t_in_ch']:>9.1f}{r['dur']:>7.1f}"
              f"{r['sim_anchor']:>9.4f}{r['sim_centroid']:>9.4f}"
              f"{(r['sim_prev'] if r['sim_prev'] is not None else float('nan')):>9.4f}")

    print(f'\n  对照：最不像参考音的 {min(args.top,8)} 段（按 anchor 升序，现有门的口径）:')
    for r in sorted(all_rows, key=lambda x: x['sim_anchor'])[:min(args.top, 8)]:
        print(f"    ch{r['ch']:<2d}{r['seg']:>6}{r['t_in_ch']:>9.1f}{r['dur']:>7.1f}"
              f"{r['sim_anchor']:>9.4f}{r['sim_centroid']:>9.4f}")

    if args.save:
        os.makedirs(os.path.join(EVAL, 'reports'), exist_ok=True)
        tag = 'consistency_' + '_'.join(str(c) for c in args.ch)
        dst = os.path.join(EVAL, 'reports', f'{tag}.json')
        with open(dst, 'w', encoding='utf-8') as f:
            json.dump(dict(rows=all_rows,
                           stats=dict(n=len(all_rows), anchor_mean=float(sa.mean()),
                                      centroid_med=med, centroid_3mad=3 * mad,
                                      gate_bad=len(gate_bad), outliers=len(outl),
                                      extra=len(outl) - len(both))),
                      f, ensure_ascii=False, indent=1)
        print(f'\n-> {dst}')


if __name__ == '__main__':
    main()
