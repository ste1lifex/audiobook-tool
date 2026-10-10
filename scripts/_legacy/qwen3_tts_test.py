# -*- coding: utf-8 -*-
"""
用 Qwen3-TTS 0.6B-CustomVoice 合成一段中文，验证环境可用。
用法: envs\qwen3tts\Scripts\python.exe scripts\qwen3_tts_test.py
"""
import os
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, 'Qwen3-TTS', 'pretrained_models', 'Qwen3-TTS-12Hz-0.6B-CustomVoice')
OUT_DIR = os.path.join(ROOT, 'output')
os.makedirs(OUT_DIR, exist_ok=True)


def main():
    print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())
    model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR,
        device_map='cuda:0' if torch.cuda.is_available() else 'cpu',
        dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    )
    print('支持的音色:', model.get_supported_speakers())

    wavs, sr = model.generate_custom_voice(
        text='清晨的薄雾笼罩着海伯利安，远方的群山在曙光中若隐若现。',
        language='Chinese',
        speaker='Vivian',
        instruct='用平静而富有叙述感的语气朗读。',
    )
    out = os.path.join(OUT_DIR, 'qwen3_tts_test.wav')
    sf.write(out, wavs[0], sr)
    print('已保存:', out, 'sr =', sr)


if __name__ == '__main__':
    main()
