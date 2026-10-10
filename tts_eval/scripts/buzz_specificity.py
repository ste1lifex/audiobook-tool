# -*- coding: utf-8 -*-
"""
电音指标的特异性验证：在用户"已听过、大体接受"的现有书稿段落上测 6-9k 高频平坦度，
确认这个指标不会误报，才敢把它加进质检门。

同时给出分档基准：
  真实人声        0.16 - 0.30
  LongCat(电音)   0.0268        <- 应被拦下
"""
import glob
import os
import random
import sys

import numpy as np
import soundfile as sf

EVAL = r'tts_eval'
CHUNKS = r'output\海伯利安\_chunks'


def metrics(w, sr):
    import librosa
    w = np.asarray(w, dtype=np.float32)
    if w.ndim > 1:
        w = w.mean(axis=1)
    if len(w) < sr * 1.0:
        return None
    S = np.abs(librosa.stft(w, n_fft=1024, hop_length=256)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    e = S.sum(axis=0)
    lvl = np.percentile(e, 90)
    sel = e >= max(np.percentile(e, 50), 0.12 * lvl)
    Sa = S[:, sel]
    out = {}
    for lo, hi, nm in ((4000, 6000, '4-6k'), (6000, 9000, '6-9k')):
        m = (freqs >= lo) & (freqs < hi)
        seg = Sa[m, :]
        out[nm] = float(np.mean(np.exp(np.mean(np.log(seg + 1e-10), axis=0))
                                / (np.mean(seg, axis=0) + 1e-10)))
    return out


print('=== 现有书稿段落（你已听过、大体接受）===')
files = sorted(glob.glob(os.path.join(CHUNKS, '*.wav')))
random.seed(3)
sample = random.sample(files, min(80, len(files)))
vals = {'4-6k': [], '6-9k': []}
for p in sample:
    w, sr = sf.read(p)
    m = metrics(w, sr)
    if m:
        for k in vals:
            vals[k].append(m[k])
for k, v in vals.items():
    a = np.array(v)
    print(f'  {k}: 均值 {a.mean():.4f}  中位 {np.median(a):.4f}  '
          f'P1 {np.percentile(a,1):.4f}  最小 {a.min():.4f}  最大 {a.max():.4f}')
a = np.array(vals['6-9k'])
for th in (0.05, 0.07, 0.08, 0.10):
    print(f'  6-9k < {th}: {(a < th).sum()}/{len(a)} 段 ({(a<th).mean():.1%})')

print('\n=== 评测三路（同一指标，同一函数）===')
print(f'{"引擎":<16}{"4-6k":>10}{"6-9k":>10}')
for eng in ('qwen3-0.6b', 'qwen3-1.7b', 'longcat-1b'):
    d = os.path.join(EVAL, 'audio', eng)
    fs = sorted(glob.glob(os.path.join(d, 'seg*.wav')))
    acc = {'4-6k': [], '6-9k': []}
    for p in fs:
        w, sr = sf.read(p)
        m = metrics(w, sr)
        if m:
            for k in acc:
                acc[k].append(m[k])
    print(f'{eng:<16}{np.mean(acc["4-6k"]):>10.4f}{np.mean(acc["6-9k"]):>10.4f}')
print(f'{"真人基准":<16}{"0.32-0.36":>10}{"0.16-0.30":>10}')
