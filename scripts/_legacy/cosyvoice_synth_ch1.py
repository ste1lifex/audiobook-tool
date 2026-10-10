# -*- coding: utf-8 -*-
"""
用 CosyVoice2 零样本克隆参考音色，合成《海伯利安》第一部第一章。
v2: 修复 wetext 前端 + 使用完整参考片段 + 合并短句
用法: envs\cosyvoice\Scripts\python.exe scripts\cosyvoice_synth_ch1.py
"""
import sys, os, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'CosyVoice'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'CosyVoice', 'third_party', 'Matcha-TTS'))

import torch
import torchaudio
from cosyvoice.cli.cosyvoice import AutoModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, 'CosyVoice', 'pretrained_models', 'CosyVoice2-0.5B')
TEXT_FILE = os.path.join(ROOT, 'epub_extract', 'book1_ch1_final.txt')
PROMPT_AUDIO = os.path.join(ROOT, 'reference_audio', 'ref_clip_full.wav')
PROMPT_TEXT = ''
OUT_DIR = os.path.join(ROOT, 'output', 'book1_ch1_v2')
os.makedirs(OUT_DIR, exist_ok=True)


def split_sentences(text):
    """按句末标点切句，短句并入前一句，避免低于提示文本长度导致质量差。"""
    parts = re.split(r'(?<=[。！？；])', text)
    sentences = []
    buf = ''
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if buf and len(buf) < 25 and not buf.endswith(('。', '！', '？')):
            buf += p
        elif buf and len(buf) >= 25 and len(p) < 12:
            buf += p
        else:
            if buf:
                sentences.append(buf)
            buf = p
    if buf:
        sentences.append(buf)
    # 再合并过短句子
    merged = []
    for s in sentences:
        if merged and len(s) < 12 and not s.endswith(('。', '！', '？')):
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def main():
    use_gpu = torch.cuda.is_available()
    print(f'CUDA available: {use_gpu}')
    cosyvoice = AutoModel(model_dir=MODEL_DIR, load_jit=False, load_trt=False,
                          fp16=use_gpu)
    print('前端:', cosyvoice.frontend.text_frontend)

    with open(TEXT_FILE, encoding='utf-8') as f:
        text = f.read()
    sentences = split_sentences(text)
    print(f'总字数 {len(text.replace(chr(10), ""))}, 切分 {len(sentences)} 句')

    prompt_text = PROMPT_TEXT
    if os.path.exists(os.path.join(ROOT, 'reference_audio', 'ref_prompt_full.txt')):
        with open(os.path.join(ROOT, 'reference_audio', 'ref_prompt_full.txt'), encoding='utf-8') as f:
            prompt_text = f.read().strip()
    print('提示文本:', prompt_text)

    sr = cosyvoice.sample_rate
    all_chunks = []
    total = len(sentences)
    for idx, sent in enumerate(sentences):
        try:
            print(f'[{idx+1}/{total}] 合成 {len(sent)} 字 ...')
            for j, out in enumerate(cosyvoice.inference_zero_shot(
                    sent, prompt_text, PROMPT_AUDIO, stream=False)):
                chunk_path = os.path.join(OUT_DIR, f'chunk_{idx:04d}.wav')
                torchaudio.save(chunk_path, out['tts_speech'], sr)
                all_chunks.append(chunk_path)
        except Exception as e:
            print(f'!! 第 {idx+1} 句失败: {e}')
            continue

    print(f'完成，共生成 {len(all_chunks)} 个音频块 -> {OUT_DIR}')


if __name__ == '__main__':
    main()
