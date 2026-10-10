# -*- coding: utf-8 -*-
"""
IndexTTS-2.5 测试：用参考音色参考音频克隆音色，合成《海伯利安》第一章开头。
用法: 在 IndexTTS 仓库根目录运行:  .venv\Scripts\python.exe ..\scripts\indextts_test.py
"""
import os, sys, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'IndexTTS'))

os.environ['HF_HUB_CACHE'] = os.path.join(ROOT, 'IndexTTS', 'checkpoints', 'hf_cache')

from indextts.infer_v2_5 import IndexTTS2

CHECKPOINTS = os.path.join(ROOT, 'IndexTTS', 'checkpoints')
REF_AUDIO = os.path.join(ROOT, 'reference_audio', 'ref_clip_full.wav')
# 一段较长的中性测试叙述（约 200 字，自拟文本，与任何出版物无关）
TEXT = (
    '清晨的光线从高高的窗户斜射进来，落在长长的木桌上。桌上摊着一张巨大的地图，'
    '几支铅笔压着边角，旁边是一只已经凉透的茶杯。窗外的院子里，几只麻雀在屋檐下跳来跳去，'
    '时不时叽叽喳喳地叫上两声。走廊尽头传来脚步声，由远及近，又在门口停住了。'
    '他抬起头，看见一个提着旧皮箱的旅人站在那里，外套上还沾着路上的灰尘。'
    '旅人摘下帽子，微微欠身，说他是从很远的地方来的，想在这里借宿一晚。'
)
OUT_DIR = os.path.join(ROOT, 'output')
os.makedirs(OUT_DIR, exist_ok=True)


def main():
    tts = IndexTTS2(
        cfg_path=os.path.join(CHECKPOINTS, 'config.yaml'),
        model_dir=CHECKPOINTS,
        use_bf16=True,
        use_cuda_kernel=True,
        use_qwen_emo=True,
    )
    print('>> 模型加载完成')

    cases = [
        ('indextts_25_plain.wav', 250, None, None),          # 无情感控制
        ('indextts_25_calm.wav', 250, '以平静沉稳的旁白语气讲述故事，像一个说书人在娓娓道来。', None),
    ]
    for out_name, max_tokens, emo_text, emo_vec in cases:
        out = os.path.join(OUT_DIR, out_name)
        kwargs = dict(
            spk_audio_prompt=REF_AUDIO,
            text=TEXT,
            lang='ZH',
            output_path=out,
            max_text_tokens_per_segment=max_tokens,
            interval_silence=250,
            verbose=False,
        )
        if emo_text:
            kwargs['use_emo_text'] = True
            kwargs['emo_text'] = emo_text
        if emo_vec:
            kwargs['emo_vector'] = emo_vec
        print(f'>> 开始合成 {out_name} (emo_text={emo_text}) ...')
        tts.infer(**kwargs)
        print(f'>> 完成: {out}')


if __name__ == '__main__':
    main()