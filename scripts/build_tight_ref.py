# -*- coding: utf-8 -*-
"""构造更干净的参考音频：去掉参考clip里的大段静音，让语音连续。
保留原始文本（已验证与音频内容一致），只压缩静音。"""
import os

import numpy as np
import soundfile as sf

SRC = r'reference_audio\ref_clip_full.wav'
OUT = r'reference_audio\ref_tight.wav'
SR = 24000
TH = 0.006          # 语音门限
PAD = 0.10          # 每段语音前后保留
MAX_GAP = 0.45      # 超过此长度的静音压缩到 GAP_KEEP
GAP_KEEP = 0.18

w, sr = sf.read(SRC)
if w.ndim > 1:
    w = w.mean(axis=1)
print(f'原始: {len(w)/sr:.2f}s @ {sr}Hz')
if sr != SR:
    import librosa
    w = librosa.resample(w.astype(np.float32), orig_sr=sr, target_sr=SR)
    sr = SR

# 找语音区间
fr = int(sr * 0.02)
n = len(w) // fr
rms = np.sqrt(np.mean(w[:fr * n].reshape(-1, fr).astype(np.float64) ** 2, axis=1))
speech = rms >= TH
spans, s = [], None
for i, v in enumerate(speech):
    if v and s is None:
        s = i
    elif not v and s is not None:
        spans.append([s * 0.02, i * 0.02])
        s = None
if s is not None:
    spans.append([s * 0.02, len(speech) * 0.02])
print(f'语音区间 {len(spans)} 段: ' + ', '.join(f'{a:.2f}-{b:.2f}' for a, b in spans))

# 合并相邻区间（间隔很小就并起来），再按 MAX_GAP 压缩
merged = []
for a, b in spans:
    if merged and a - merged[-1][1] <= 0.30:
        merged[-1][1] = b
    else:
        merged.append([a, b])
print(f'合并后 {len(merged)} 段')

pieces = []
for i, (a, b) in enumerate(merged):
    s0 = max(0, int((a - PAD) * sr))
    s1 = min(len(w), int((b + PAD) * sr))
    pieces.append(w[s0:s1])
    if i < len(merged) - 1:
        gap = merged[i + 1][0] - b
        keep = GAP_KEEP if gap > MAX_GAP else min(gap, GAP_KEEP)
        pieces.append(np.zeros(int(keep * sr), dtype=w.dtype))

out = np.concatenate(pieces).astype(np.float32)
# 峰值归一化到 -3dBFS 附近，避免过弱
pk = np.max(np.abs(out))
if pk > 0:
    out = out * (0.7 / pk)
sf.write(OUT, out, SR)
print(f'输出: {OUT}')
print(f'长度 {len(out)/sr:.2f}s (原 {len(w)/sr:.2f}s)  峰值归一化 0.7')
