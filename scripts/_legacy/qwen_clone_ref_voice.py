# -*- coding: utf-8 -*-
"""
Qwen3-TTS 0.6B-Base：克隆参考音色，合成《海伯利安》第一章开头。
用法: envs\qwen3tts\Scripts\python.exe scripts\qwen_clone_ref_voice.py
"""
import os
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, 'Qwen3-TTS', 'pretrained_models', 'Qwen3-TTS-12Hz-0.6B-Base')
REF_AUDIO = os.path.join(ROOT, 'reference_audio', 'ref_clip_full.wav')
REF_TEXT = ''
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
    print('>> Base 模型加载完成')

    # 只用说话人嵌入克隆（不依赖转写文本，稳健）
    wavs, sr = model.generate_voice_clone(
        text=TEXT,
        language='Chinese',
        ref_audio=REF_AUDIO,
        ref_text=REF_TEXT,
        x_vector_only_mode=False,
    )
    out = os.path.join(OUT_DIR, 'qwen_clone_ref_voice.wav')
    sf.write(out, wavs[0], sr)
    print('已保存:', out, 'sr =', sr, '时长约', round(len(wavs[0]) / sr, 1), '秒')

    # 纯 x-vector 模式对比（只用音色）
    wavs2, sr2 = model.generate_voice_clone(
        text=TEXT[:60],
        language='Chinese',
        ref_audio=REF_AUDIO,
        ref_text=REF_TEXT,
        x_vector_only_mode=True,
    )
    out2 = os.path.join(OUT_DIR, 'qwen_clone_ref_voice_xvec.wav')
    sf.write(out2, wavs2[0], sr2)
    print('已保存:', out2, 'sr =', sr2)


if __name__ == '__main__':
    main()