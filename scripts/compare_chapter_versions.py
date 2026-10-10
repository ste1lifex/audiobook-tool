# -*- coding: utf-8 -*-
"""
同一章的新旧版本对比：用统一锚点打分，量化"改进前 vs 改进后"。

用法:
  python compare_chapter_versions.py --book 海伯利安 --ch 7
  python compare_chapter_versions.py --book 海伯利安 --ch 7 --old _chunks --new _chunks_v2
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quality import SegmentGate, SpeakerScorer, spectral_flatness  # noqa: E402

import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--book', default='海伯利安')
ap.add_argument('--ch', type=int, default=7)
ap.add_argument('--old', default='_chunks')
ap.add_argument('--new', default='_chunks_v2')
ap.add_argument('--ref', default=config.DEFAULT_REF_AUDIO)
args = ap.parse_args()

book_dir = os.path.join(config.OUTPUT_BASE, args.book)
prefix = f"{args.book}_ch{args.ch:02d}"

# 统一用 ref_clip_full 当锚点，保证新旧可比
ANCHOR = r'reference_audio\ref_clip_full.wav'
sc = SpeakerScorer()
aw, asr = sf.read(ANCHOR)
if aw.ndim > 1:
    aw = aw.mean(axis=1)
anchor = sc.embed(aw, asr)
gate = SegmentGate(args.ref, scorer=sc)


def scan(subdir):
    files = sorted(glob.glob(os.path.join(book_dir, subdir, f'{prefix}_seg*.wav')))
    rows = []
    for p in files:
        w, sr = sf.read(p)
        if w.ndim > 1:
            w = w.mean(axis=1)
        if len(w) < sr:
            continue
        e = sc.embed(w, sr)
        ok, why, m = gate.check(w, sr)
        rows.append(dict(
            seg=int(re.search(r'_seg(\d+)\.wav$', p).group(1)),
            sim=float(np.dot(e, anchor)),
            flat=spectral_flatness(w, sr),
            win=m.get('win_sim', np.nan),
            dur=len(w) / sr,
            ok=ok, why=why))
    return rows


def report(name, rows):
    if not rows:
        print(f'\n[{name}] 无数据')
        return None
    sims = np.array([r['sim'] for r in rows])
    flat = np.array([r['flat'] for r in rows])
    dur = np.array([r['dur'] for r in rows])
    bad = [r for r in rows if not r['ok']]
    print(f'\n===== {name}  (n={len(rows)}) =====')
    print(f'  声纹相似度 : 均值 {sims.mean():.4f}  标准差 {sims.std():.4f}  '
          f'最低 {sims.min():.4f}  P5 {np.percentile(sims,5):.4f}')
    print(f'  谱平坦度   : 均值 {flat.mean():.4f}  最大 {flat.max():.4f}  '
          f'P95 {np.percentile(flat,95):.4f}')
    print(f'  段时长     : 均值 {dur.mean():.1f}s  最大 {dur.max():.1f}s')
    print(f'  质检不合格 : {len(bad)}/{len(rows)} ({len(bad)/len(rows):.1%})')
    print(f'  相似度 <0.62 的段: {sum(1 for s in sims if s < 0.62)}')
    print(f'  总时长     : {dur.sum()/60:.1f} 分钟')
    if bad:
        print(f'  不合格明细(前12):')
        for r in sorted(bad, key=lambda x: x['sim'])[:12]:
            print(f'    seg{r["seg"]:05d} {r["why"]:<28} sim={r["sim"]:.3f} '
                  f'flat={r["flat"]:.3f} dur={r["dur"]:.0f}s')
    return dict(n=len(rows), sim=sims, flat=flat, dur=dur, bad=len(bad))


old = scan(args.old)
new = scan(args.new)
ro = report(f'旧版 {args.old}', old)
rn = report(f'新版 {args.new}', new)

if ro and rn:
    print('\n===== 改进对比 =====')
    print(f'{"指标":<16}{"旧版":>12}{"新版":>12}{"变化":>14}')
    print(f'{"相似度均值":<16}{ro["sim"].mean():>12.4f}{rn["sim"].mean():>12.4f}'
          f'{rn["sim"].mean()-ro["sim"].mean():>+14.4f}')
    print(f'{"相似度标准差":<16}{ro["sim"].std():>12.4f}{rn["sim"].std():>12.4f}'
          f'{rn["sim"].std()-ro["sim"].std():>+14.4f}')
    print(f'{"相似度最低":<16}{ro["sim"].min():>12.4f}{rn["sim"].min():>12.4f}'
          f'{rn["sim"].min()-ro["sim"].min():>+14.4f}')
    print(f'{"平坦度均值":<16}{ro["flat"].mean():>12.4f}{rn["flat"].mean():>12.4f}'
          f'{rn["flat"].mean()-ro["flat"].mean():>+14.4f}')
    print(f'{"平坦度最大":<16}{ro["flat"].max():>12.4f}{rn["flat"].max():>12.4f}'
          f'{rn["flat"].max()-ro["flat"].max():>+14.4f}')
    print(f'{"不合格段数":<16}{ro["bad"]:>12d}{rn["bad"]:>12d}'
          f'{rn["bad"]-ro["bad"]:>+14d}')
    print(f'{"总时长(分)":<16}{ro["dur"].sum()/60:>12.1f}{rn["dur"].sum()/60:>12.1f}'
          f'{(rn["dur"].sum()-ro["dur"].sum())/60:>+14.1f}')
