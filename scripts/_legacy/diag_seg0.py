# -*- coding: utf-8 -*-
"""诊断：第一章第一段，带 max_new_tokens 上限，计时。"""
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
print(f"seg0: {len(segs[0])} 字 | seg1: {len(segs[1])} 字")

t0 = time.time()
model = Qwen3TTSModel.from_pretrained(MODEL_DIR, device_map='cuda:0', dtype=torch.bfloat16)
print(f"load {time.time()-t0:.1f}s")
prompt = model.create_voice_clone_prompt(ref_audio=REF_AUDIO, ref_text=REF_TEXT, x_vector_only_mode=False)

# 测试不同 max_new_tokens / 段
cases = [
    ("seg0_max1500", segs[0], 1500),
    ("seg0_max2000", segs[0], 2000),
]
for name, seg, mnt in cases:
    t0 = time.time()
    wavs, sr = model.generate_voice_clone(
        text=seg, language='Chinese', voice_clone_prompt=prompt,
        max_new_tokens=mnt,
    )
    dt = time.time() - t0
    d = len(wavs[0]) / sr
    print(f"{name}: {dt:.1f}s, audio {d:.1f}s, RTF {dt/d:.2f}", flush=True)
    sf.write(os.path.join(ROOT, r"output", f"{name}.wav"), wavs[0], sr)