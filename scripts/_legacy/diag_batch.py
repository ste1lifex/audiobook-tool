# -*- coding: utf-8 -*-
"""对比：单段 vs 4段 batch 生成，测吞吐是否有提升。"""
import os, time
import torch
import soundfile as sf
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from batch_synth import split_segments
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MODEL_DIR = os.path.join(ROOT, r"Qwen3-TTS\pretrained_models\Qwen3-TTS-12Hz-0.6B-Base")
REF_AUDIO = os.path.join(ROOT, r"reference_audio\ref_clip_full.wav")
REF_TEXT = ""

with open(os.path.join(ROOT, r"epub_extract\simplified\book1_part0006.txt"), encoding="utf-8") as f:
    text = f.read()
segs = split_segments(text)
model = Qwen3TTSModel.from_pretrained(MODEL_DIR, device_map='cuda:0', dtype=torch.bfloat16)
prompt = model.create_voice_clone_prompt(ref_audio=REF_AUDIO, ref_text=REF_TEXT, x_vector_only_mode=False)

# 单段 x4（模拟当前串行）
t0 = time.time()
for s in segs[:4]:
    wavs, sr = model.generate_voice_clone(text=s, language='Chinese', voice_clone_prompt=prompt, max_new_tokens=1600)
t_serial = time.time() - t0
d_serial = sum(len(w)/sr for w in [wavs[0]]*0)  # 略
print(f"串行 4 段: {t_serial:.1f}s", flush=True)

# batch 4 段
t0 = time.time()
wavs_b, sr = model.generate_voice_clone(
    text=segs[:4], language=['Chinese']*4, voice_clone_prompt=prompt,
    max_new_tokens=1600,
)
t_batch = time.time() - t0
dur_b = sum(len(w)/sr for w in wavs_b)
print(f"batch 4 段: {t_batch:.1f}s, 音频 {dur_b:.1f}s, RTF {t_batch/dur_b:.2f}", flush=True)
for i, w in enumerate(wavs_b):
    sf.write(os.path.join(ROOT, r"output", f"batch_test_{i}.wav"), w, sr)
print(f"串行估算: {t_serial:.1f}s / batch: {t_batch:.1f}s -> 加速 {t_serial/t_batch:.2f}x", flush=True)