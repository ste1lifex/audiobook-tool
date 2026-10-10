# -*- coding: utf-8 -*-
"""把指定 chunk 目录拼成章节 mp3，并复核停顿节奏。"""
import argparse
import glob
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--book', default='海伯利安')
ap.add_argument('--ch', type=int, default=7)
ap.add_argument('--chunks', default='_chunks_v3')
ap.add_argument('--suffix', default='v3')
args = ap.parse_args()

BOOK = os.path.join(config.OUTPUT_BASE, args.book)
PREFIX = f'{args.book}_ch{args.ch:02d}'
files = glob.glob(os.path.join(BOOK, args.chunks, f'{PREFIX}_seg*.wav'))
def idx(p):
    return int(re.search(r'_seg(\d+)\.wav$', p).group(1))
files.sort(key=idx)
print(f'拼接 {len(files)} 段 <- {args.chunks}')

import json  # noqa: E402
chs = json.load(open(os.path.join(BOOK, '_chapters.json'), encoding='utf-8'))
ch = [c for c in chs if c['index'] == args.ch][0]

sil = np.zeros(int(config.SAMPLE_RATE * config.SEG_SILENCE_SEC), dtype=np.float32)
parts = []
for k, p in enumerate(files):
    w, sr = sf.read(p)
    if w.ndim > 1:
        w = w.mean(axis=1)
    if w.dtype != np.float32:
        w = w.astype(np.float32)
    parts.append(w)
    if k < len(files) - 1:
        parts.append(sil)
full = np.concatenate(parts)
print(f'总长 {len(full)/config.SAMPLE_RATE/60:.1f} 分钟')

wav = os.path.join(BOOK, f'{PREFIX}_全章_{args.suffix}.wav')
mp3 = os.path.join(BOOK, f'{PREFIX}_{ch["title"]}_{args.suffix}.mp3')
sf.write(wav, full, config.SAMPLE_RATE)
subprocess.run([config.FFMPEG, '-y', '-v', 'error', '-i', wav, '-codec:a', 'libmp3lame',
                '-qscale:a', '2', mp3], check=True)
os.remove(wav)
print(f'-> {mp3}  ({os.path.getsize(mp3)/1024/1024:.1f} MB)')

# 复核停顿
FRAME, MIN_P = 0.02, 0.25
fr = int(config.SAMPLE_RATE * FRAME)
n = len(full) // fr
rms = np.sqrt(np.mean(full[:fr*n].reshape(-1, fr).astype(np.float64)**2, axis=1))
th = max(0.004, 0.12 * float(np.percentile(rms, 90)))
voiced = rms >= th
pauses, i = [], 0
while i < n:
    if not voiced[i]:
        j = i
        while j < n and not voiced[j]:
            j += 1
        pl = (j - i) * FRAME
        if pl >= MIN_P:
            pauses.append(pl)
        i = j
    else:
        i += 1
pa = np.array(pauses)
print(f'\n停顿复核: 个数 {len(pa)}  平均 {pa.mean():.2f}s  中位 {np.median(pa):.2f}s  '
      f'p75 {np.percentile(pa,75):.2f}s  p90 {np.percentile(pa,90):.2f}s  '
      f'≥1s {int((pa>=1).sum())}')
print(f'停顿合计 {pa.sum()/60:.1f} 分钟  占比 {pa.sum()/(len(full)/config.SAMPLE_RATE):.1%}')
print(f'  对照旧版: 平均 0.75s 中位 0.68s p75 0.92s p90 1.20s ≥1s 938 停顿 58.0m 占比 34.8%')
