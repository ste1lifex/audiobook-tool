# -*- coding: utf-8 -*-
"""计时测试：Qwen3-TTS 克隆参考音色，测 RTF 和每千字耗时。"""
import os, time
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, 'Qwen3-TTS', 'pretrained_models', 'Qwen3-TTS-12Hz-0.6B-Base')
REF_AUDIO = os.path.join(ROOT, 'reference_audio', 'ref_clip_full.wav')
REF_TEXT = ''

with open(os.path.join(ROOT, 'epub_extract', 'simplified', 'book1_part0005.txt'), encoding='utf-8') as f:
    ch1 = f.read()
# 取开头约 800 字
text = ch1[:800]

t0 = time.time()
model = Qwen3TTSModel.from_pretrained(
    MODEL_DIR, device_map='cuda:0', dtype=torch.bfloat16,
)
t_load = time.time() - t0
print(f'模型加载: {t_load:.1f}s')

t0 = time.time()
wavs, sr = model.generate_voice_clone(
    text=text, language='Chinese',
    ref_audio=REF_AUDIO, ref_text=REF_TEXT,
    x_vector_only_mode=False,
)
t_gen = time.time() - t0
dur = len(wavs[0]) / sr
chars = len(text.replace('\n', ''))
print(f'合成: {t_gen:.1f}s, 音频 {dur:.1f}s, 文本 {chars} 字')
print(f'RTF = {t_gen / dur:.2f}')
print(f'速度 = {chars / t_gen:.1f} 字/秒生成')
print(f'预估 2h(7200s 音频) 需 {int(7200 * chars / dur)} 字, 耗时 {t_gen * 7200 / dur / 3600:.1f} 小时')
sf.write(os.path.join(ROOT, 'output', 'timing_test.wav'), wavs[0], sr)
print('已存 timing_test.wav')