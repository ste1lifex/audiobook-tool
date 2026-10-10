# -*- coding: utf-8 -*-
"""
Qwen3-TTS 长文本叙述测试：用 CustomVoice 0.6B 合成一段较长的叙述文字，
对比 CosyVoice 的逐句拼接效果。注意：本模型暂未克隆参考音色，
用内置 narrator 风格音色测试"叙述感"。
用法: envs\qwen3tts\Scripts\python.exe scripts\qwen3_long_test.py
"""
import os
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, 'Qwen3-TTS', 'pretrained_models', 'Qwen3-TTS-12Hz-0.6B-CustomVoice')
OUT_DIR = os.path.join(ROOT, 'output')
os.makedirs(OUT_DIR, exist_ok=True)

# 一段较长的中性测试叙述（约 200 字，自拟文本，与任何出版物无关）
TEXT = (
    '清晨的光线从高高的窗户斜射进来，落在长长的木桌上。桌上摊着一张巨大的地图，'
    '几支铅笔压着边角，旁边是一只已经凉透的茶杯。窗外的院子里，几只麻雀在屋檐下跳来跳去，'
    '时不时叽叽喳喳地叫上两声。走廊尽头传来脚步声，由远及近，又在门口停住了。'
    '他抬起头，看见一个提着旧皮箱的旅人站在那里，外套上还沾着路上的灰尘。'
    '旅人摘下帽子，微微欠身，说他是从很远的地方来的，想在这里借宿一晚。'
)


def main():
    print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())
    model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR,
        device_map='cuda:0' if torch.cuda.is_available() else 'cpu',
        dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
    )
    # 试几种叙述型指令
    instructs = [
        '用低沉平静、富有叙述感的声音，像说书人一样娓娓道来，语速适中。',
        '以沉稳的男声旁白风格朗读，情绪平静克制，注意停顿和节奏。',
    ]
    for i, instruct in enumerate(instructs):
        wavs, sr = model.generate_custom_voice(
            text=TEXT,
            language='Chinese',
            speaker='Serena',
            instruct=instruct,
        )
        out = os.path.join(OUT_DIR, f'qwen_long_test_{i}.wav')
        sf.write(out, wavs[0], sr)
        print('已保存:', out, 'sr =', sr, '时长约', len(wavs[0]) / sr, '秒')


if __name__ == '__main__':
    main()