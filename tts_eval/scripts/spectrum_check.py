# -*- coding: utf-8 -*-
"""
频谱对比 + 高频含量量化：检查 LongCat 极低的谱平坦度是"真干净"还是"过度平滑/频带被压窄"。
跑在 envs\\qwen3tts（有 librosa / PIL）。
"""
import os
import sys

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

EVAL = r'tts_eval'
ENGINES = [('qwen3-0.6b', 'Qwen3-0.6B'), ('qwen3-1.7b', 'Qwen3-1.7B'),
           ('longcat-1b', 'LongCat-1B')]
SEG = 2
OUT = os.path.join(EVAL, 'reports', 'spectrum_compare.png')


def load(eng, idx):
    p = os.path.join(EVAL, 'audio', eng, f'seg{idx:02d}.wav')
    w, sr = sf.read(p)
    if w.ndim > 1:
        w = w.mean(axis=1)
    return w.astype(np.float32), sr


def hf_stats(w, sr, n_fft=1024, hop=256):
    import librosa
    S = np.abs(librosa.stft(w, n_fft=n_fft, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    e = S.sum(axis=0)
    lvl = np.percentile(e, 90)
    sel = e >= max(np.percentile(e, 50), 0.12 * lvl)
    Sa = S[:, sel]
    tot = Sa.sum() + 1e-12
    out = {}
    for lo, hi, name in ((0, 4000, '<4k'), (4000, 8000, '4-8k'),
                         (8000, 12000, '8-12k')):
        m = (freqs >= lo) & (freqs < hi)
        out[name] = float(Sa[m, :].sum() / tot)
    cent = (freqs[:, None] * Sa).sum() / tot
    out['centroid'] = float(cent)
    # 高频段的谱平坦度（看高频是谐波还是噪声）
    hi_m = freqs >= 6000
    Sh = Sa[hi_m, :]
    gm = np.exp(np.mean(np.log(Sh + 1e-10), axis=0))
    am = np.mean(Sh, axis=0)
    out['hf_flat'] = float(np.mean(gm / (am + 1e-10)))
    return out


print(f'{"引擎":<14}{"<4k":>8}{"4-8k":>8}{"8-12k":>8}{"质心Hz":>9}{"高频平坦度":>11}')
for eng, lab in ENGINES:
    w, sr = load(eng, SEG)
    s = hf_stats(w, sr)
    print(f'{lab:<14}{s["<4k"]:>7.1%}{s["4-8k"]:>8.1%}{s["8-12k"]:>8.1%}'
          f'{s["centroid"]:>9.0f}{s["hf_flat"]:>11.4f}')

# ---- 拼频谱图 ----
W_PER_SEC, H, LAB_H, GAP = 90, 190, 22, 8
pieces = []
for eng, lab in ENGINES:
    w, sr = load(eng, SEG)
    pieces.append((w[:int(sr * 8)], sr, lab))
img_w = int(W_PER_SEC * 8)
img_h = len(pieces) * (H + LAB_H + GAP)
img = Image.new('RGB', (img_w, img_h), (16, 16, 20))
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 13)
except Exception:
    font = ImageFont.load_default()

STOPS = np.array([[0.267, .005, .329], [.283, .141, .458], [.254, .265, .530],
                  [.207, .372, .553], [.164, .471, .558], [.128, .567, .551],
                  [.135, .659, .518], [.267, .749, .441], [.478, .821, .318],
                  [.741, .873, .150], [.993, .906, .144]])

import librosa  # noqa: E402
for i, (w, sr, lab) in enumerate(pieces):
    y = i * (H + LAB_H + GAP)
    M = np.abs(librosa.stft(w, n_fft=1024, hop_length=256)) ** 2
    mel = librosa.feature.melspectrogram(S=M, sr=sr, n_mels=128, fmin=40, fmax=sr // 2)
    L = librosa.power_to_db(mel, ref=np.max)
    N = np.clip((L + 70) / 70, 0, 1)
    idx = N * (len(STOPS) - 1)
    lo = np.floor(idx).astype(int).clip(0, len(STOPS) - 2)
    fr = (idx - lo)[..., None]
    rgb = (STOPS[lo] * (1 - fr) + STOPS[lo + 1] * fr) * 255
    im = Image.fromarray(rgb.astype(np.uint8), 'RGB').resize((img_w, H), Image.BILINEAR)
    im = im.transpose(Image.FLIP_TOP_BOTTOM)
    img.paste(im, (0, y + LAB_H))
    draw.rectangle([0, y, img_w, y + LAB_H - 1], fill=(30, 30, 38))
    draw.text((6, y + 4), f'{lab}   seg{SEG:02d}  前 8 秒', fill=(235, 235, 240), font=font)
img.save(OUT)
print(f'\n-> {OUT}')
