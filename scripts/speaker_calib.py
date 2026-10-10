# -*- coding: utf-8 -*-
"""声纹打分器标定：确认余弦相似度真的能区分"同一人/不同内容/纯噪声"。"""
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_quality_audit import SpeakerScorer  # noqa: E402

FFMPEG = r'bin\ffmpeg.exe'
REF = r'reference_audio\ref_clip_full.wav'
SRC = r'reference_audio\ref_source_audio.m4a'
TMP = r'reference_audio\_calib_other.wav'

sc = SpeakerScorer()
rw, rsr = sf.read(REF)
if rw.ndim > 1:
    rw = rw.mean(axis=1)
ref = sc.embed(rw, rsr)


def score(path, label):
    w, sr = sf.read(path)
    if w.ndim > 1:
        w = w.mean(axis=1)
    e = sc.embed(w, sr)
    s = float(np.dot(e, ref))
    print(f'  {label:<42} sim={s:+.4f}  ({len(w)/sr:.1f}s)')
    return s


print('=== 标定 ===')
score(REF, '参考音频 vs 自己')
# 远处另一段参考音色台词（源音频 400-410s，是别的内容）
subprocess.run([FFMPEG, '-y', '-v', 'quiet', '-ss', '400', '-t', '10', '-i', SRC,
                '-ar', '16000', '-ac', '1', TMP], check=True)
score(TMP, '源音频 400-410s 另一段参考音色台词')
# 纯白噪声
rng = np.random.default_rng(0)
sf.write(TMP, (rng.standard_normal(24000 * 6) * 0.05).astype(np.float32), 24000)
score(TMP, '白噪声')
# 正弦音（非语音）
t = np.arange(24000 * 6) / 24000
sf.write(TMP, (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000)
score(TMP, '220Hz 正弦音')

print('=== 旧管线产物 output\\book1_ch1 ===')
for i in (0, 5, 9):
    p = rf'output\book1_ch1\chunk_{i:04d}.wav'
    if os.path.exists(p):
        score(p, os.path.basename(p))

print('=== 另一个工程的 wav（japanese/其他）===')
for p in (r'IndexTTS\.venv\Lib\site-packages\gradio\test_data\test_audio.wav',):
    if os.path.exists(p):
        score(p, 'gradio test_audio.wav')

if os.path.exists(TMP):
    os.remove(TMP)
