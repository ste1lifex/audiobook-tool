# -*- coding: utf-8 -*-
"""单段验证：重新生成 seg45，检查时长是否恢复正常。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import soundfile as sf
import numpy as np
import torch
from qwen_tts import Qwen3TTSModel
from fix_bad_segments import split_segments

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MODEL_DIR = os.path.join(ROOT, r"Qwen3-TTS\pretrained_models\Qwen3-TTS-12Hz-0.6B-Base")
REF_AUDIO = os.path.join(ROOT, r"reference_audio\ref_clip_full.wav")
REF_TEXT = ""

with open(os.path.join(ROOT, r"epub_extract\simplified\book1_part0006.txt"), encoding="utf-8") as f:
    text = f.read()
segs = split_segments(text)
seg = segs[45]
print(f"seg45: {len(seg)} 字")

model = Qwen3TTSModel.from_pretrained(MODEL_DIR, device_map="cuda:0", dtype=torch.bfloat16)
prompt = model.create_voice_clone_prompt(ref_audio=REF_AUDIO, ref_text=REF_TEXT, x_vector_only_mode=False)

for attempt in range(3):
    wavs, sr = model.generate_voice_clone(
        text=seg, language="Chinese", voice_clone_prompt=prompt, max_new_tokens=1600)
    w = wavs[0]
    d = len(w) / sr
    print(f"第{attempt+1}次: {d:.1f}s {'✓ 正常' if d <= 60 else '✗ 仍异常'}", flush=True)
    if d <= 60:
        sf.write(os.path.join(ROOT, r"output", "fix_verify_seg45.wav"), w, sr)
        print("已保存验证音频 fix_verify_seg45.wav")
        break