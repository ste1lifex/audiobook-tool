# -*- coding: utf-8 -*-
"""
关键补测：真实人声的高频平坦度基准。

我之前的错误：把"高频平坦度低"当成"更干净"。但自然语音的高频本来就含大量
非周期成分（气声/齿音/摩擦音），高频过于谐波化 = 周期性嗡鸣 = 电音。

本脚本用同一套指标量：
  * 真实人声（参考音色原声多个片段）← 自然语音的基准
  * 三个引擎的合成结果
看谁接近人声、谁是异常值。
"""
import glob
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

EVAL = r'tts_eval'
FFMPEG = r'bin\ffmpeg.exe'
SRC = r'reference_audio\ref_source_audio.m4a'
TMP = os.path.join(EVAL, 'tmp')


def metrics(w, sr, n_fft=1024, hop=256):
    import librosa
    w = np.asarray(w, dtype=np.float32)
    if w.ndim > 1:
        w = w.mean(axis=1)
    if len(w) < sr * 0.5:
        return None
    S = np.abs(librosa.stft(w, n_fft=n_fft, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    e = S.sum(axis=0)
    lvl = np.percentile(e, 90)
    sel = e >= max(np.percentile(e, 50), 0.12 * lvl)
    Sa = S[:, sel]
    tot = Sa.sum() + 1e-12
    o = {}
    o['centroid'] = float((freqs[:, None] * Sa).sum() / tot)
    for lo, hi, nm in ((2000, 4000, '2-4k'), (4000, 6000, '4-6k'),
                       (6000, 9000, '6-9k'), (9000, 12000, '9-12k')):
        m = (freqs >= lo) & (freqs < hi)
        seg = Sa[m, :]
        o[f'flat_{nm}'] = float(np.mean(
            np.exp(np.mean(np.log(seg + 1e-10), axis=0)) / (np.mean(seg, axis=0) + 1e-10)))
        o[f'e_{nm}'] = float(seg.sum() / tot)
    # 整体高频(>=5k)平坦度
    m = freqs >= 5000
    Sh = Sa[m, :]
    o['flat_hf'] = float(np.mean(
        np.exp(np.mean(np.log(Sh + 1e-10), axis=0)) / (np.mean(Sh, axis=0) + 1e-10)))
    # 谐波性指标: 自相关在基频周期处的峰值（越高越"周期嗡鸣"）
    w2 = w - w.mean()
    ac = np.correlate(w2, w2, 'full')[len(w2) - 1:]
    ac = ac / (ac[0] + 1e-12)
    lo, hi = int(sr / 400), int(sr / 70)          # 70-400Hz 基频范围
    if hi > lo and hi < len(ac):
        o['periodicity'] = float(np.max(ac[lo:hi]))
    else:
        o['periodicity'] = 0.0
    return o


def show(tag, w, sr):
    m = metrics(w, sr)
    if m is None:
        print(f'{tag:<28} (太短)')
        return None
    print(f'{tag:<28}{m["centroid"]:>9.0f}{m["flat_hf"]:>11.4f}'
          f'{m["flat_4-6k"]:>11.4f}{m["flat_6-9k"]:>11.4f}'
          f'{m["e_4-6k"]:>9.1%}{m["e_6-9k"]:>9.1%}{m["periodicity"]:>11.4f}')
    return m


os.makedirs(TMP, exist_ok=True)
print(f'{"":28}{"质心Hz":>9}{"高频平坦":>11}{"4-6k平坦":>11}{"6-9k平坦":>11}'
      f'{"4-6k能量":>9}{"6-9k能量":>9}{"周期性":>11}')
print('-' * 100)

print('【真实人声】')
for f in (r'reference_audio\ref_clip_full.wav',
          r'reference_audio\ref_tight.wav'):
    if os.path.exists(f):
        w, sr = sf.read(f)
        show('  真人 ' + os.path.basename(f), w, sr)
for ss, t in ((400, 12), (100, 12), (200, 12), (54.6, 9)):
    p = os.path.join(TMP, f'human_{int(ss)}.wav')
    subprocess.run([FFMPEG, '-y', '-v', 'quiet', '-ss', str(ss), '-t', str(t),
                    '-i', SRC, '-ar', '24000', '-ac', '1', p], check=True)
    w, sr = sf.read(p)
    show(f'  真人 源{ss}s-{ss+t}s', w, sr)

print('\n【合成】')
for eng in ('qwen3-0.6b', 'qwen3-1.7b', 'longcat-1b'):
    d = os.path.join(EVAL, 'audio', eng)
    files = sorted(glob.glob(os.path.join(d, 'seg*.wav')))[:6]
    accs = []
    for p in files:
        w, sr = sf.read(p)
        m = metrics(w, sr)
        if m:
            accs.append(m)
    if accs:
        avg = {k: float(np.mean([a[k] for a in accs])) for k in accs[0]}
        show(f'  {eng}', np.zeros(1), 24000) if False else None
        print(f'  {eng:<26}{avg["centroid"]:>9.0f}{avg["flat_hf"]:>11.4f}'
              f'{avg["flat_4-6k"]:>11.4f}{avg["flat_6-9k"]:>11.4f}'
              f'{avg["e_4-6k"]:>9.1%}{avg["e_6-9k"]:>9.1%}{avg["periodicity"]:>11.4f}')
