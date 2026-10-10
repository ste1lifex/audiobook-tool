# -*- coding: utf-8 -*-
"""
模型自检：加载 Qwen3-TTS、克隆参考音色、合成一句话并写出 _selftest.wav。
用于确认「模型 + CUDA + 参考音频 + 采样参数」这条链路是通的。约 30 秒。
"""
import os
import sys

PKG = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PKG, 'audiobook_tool'))

import config                                              # noqa: E402
import torch                                               # noqa: E402
import soundfile as sf                                     # noqa: E402
from qwen_tts import Qwen3TTSModel                         # noqa: E402

print(f'  模型目录: {config.MODEL_DIR}')
print(f'  参考音频: {config.DEFAULT_REF_AUDIO}')
model = Qwen3TTSModel.from_pretrained(config.MODEL_DIR, device_map='cuda:0',
                                      dtype=torch.bfloat16)
print('  模型加载完成，正在合成...')
prompt = model.create_voice_clone_prompt(
    ref_audio=config.DEFAULT_REF_AUDIO, ref_text=config.DEFAULT_REF_TEXT,
    x_vector_only_mode=False)          # False = ICL 克隆，音色更稳（见 config 注释）
wavs, sr = model.generate_voice_clone(
    text='安装自检，这句话应当能被正常合成，音色应当与参考音频一致。',
    language='Chinese', voice_clone_prompt=prompt, max_new_tokens=config.MAX_NEW_TOKENS,
    temperature=config.TEMPERATURE, top_p=config.TOP_P,
    repetition_penalty=config.REPETITION_PENALTY)
out = os.path.join(PKG, '_selftest.wav')
sf.write(out, wavs[0], sr)
print(f'  合成成功: {out}  ({len(wavs[0]) / sr:.1f}s)')
