# -*- coding: utf-8 -*-
"""
整夜批量合成 v2（batch 版）：Qwen3-TTS 克隆参考音色 → 《海伯利安》→ 分段批次合成。

v2 关键改进（相比 v1）:
- 批量推理: 4 段/批，RTF 0.80 vs 2.74，吞吐 ~3.5x
- 断点续跑: 单段级 chunk 检查，重启自动跳过已完成段
- 异常恢复: 单段失败隔离，不影响同批其他段
- 每章输出 wav + mp3 到 output/audiobook/

用法:
  envs\qwen3tts\Scripts\python.exe scripts\batch_synth.py
"""
import os, re, sys, time, subprocess
import torch
import soundfile as sf
import numpy as np
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, 'Qwen3-TTS', 'pretrained_models', 'Qwen3-TTS-12Hz-0.6B-Base')
REF_AUDIO = os.path.join(ROOT, 'reference_audio', 'ref_clip_full.wav')
REF_TEXT = ''
SIMPLIFIED = os.path.join(ROOT, 'epub_extract', 'simplified')
OUT_DIR = os.path.join(ROOT, 'output', 'audiobook')
CHUNK_DIR = os.path.join(OUT_DIR, '_chunks')
FFMPEG = r"bin\ffmpeg.exe"

# ===== 当晚计划：完整跑完 序 + 第一章（剧情不截断） =====
CHAPTERS = [
    ('book1_part0005.txt', '海伯利安-序'),
    ('book1_part0006.txt', '海伯利安-第一章'),
]
MAX_CHARS_PER_SEG = 205      # 每段文本长度上限
BATCH_SIZE = 4               # 批量推理大小（RTF 显著更低）
MAX_NEW_TOKENS = 1600        # 12Hz codec ≈ 133s 音频上限，防失控
SEG_SILENCE_SEC = 0.30       # 段间静音
SAMPLE_RATE = 24000


def split_segments(text, max_chars=MAX_CHARS_PER_SEG):
    text = re.sub(r'\s+', '', text)
    parts = re.split(r'(?<=[。！？；…])', text)
    segs = []
    buf = ''
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) <= max_chars:
            buf += p
        else:
            if buf:
                segs.append(buf)
                buf = p
            else:
                sub = re.split(r'(?<=[，、：])', p)
                b2 = ''
                for s2 in sub:
                    if len(b2) + len(s2) <= max_chars:
                        b2 += s2
                    else:
                        if b2:
                            segs.append(b2)
                        b2 = s2
                if b2:
                    buf = b2
    if buf:
        segs.append(buf)
    return [s for s in segs if s.strip()]


def main():
    use_cuda = torch.cuda.is_available()
    print(f'CUDA: {use_cuda} | batch={BATCH_SIZE} | 每段≤{MAX_CHARS_PER_SEG}字', flush=True)
    model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR,
        device_map='cuda:0' if use_cuda else 'cpu',
        dtype=torch.bfloat16 if use_cuda else torch.float32,
    )
    print('>> 模型加载完成', flush=True)
    prompt = model.create_voice_clone_prompt(
        ref_audio=REF_AUDIO, ref_text=REF_TEXT, x_vector_only_mode=False)
    print('>> 克隆 prompt 构建完成', flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CHUNK_DIR, exist_ok=True)

    total_audio_sec = 0.0
    grand_start = time.time()
    for fname, title in CHAPTERS:
        path = os.path.join(SIMPLIFIED, fname)
        if not os.path.exists(path):
            print(f'!! 缺文件 {path}', flush=True)
            continue
        with open(path, encoding='utf-8') as f:
            text = f.read()
        segs = split_segments(text)
        print(f'== {title}: {len(text)} 字 -> {len(segs)} 段 ==', flush=True)

        chapter_wavs = []
        # 断点续跑：跳过已有 chunk
        pending = []
        for i, seg in enumerate(segs):
            chunk_path = os.path.join(CHUNK_DIR, f'{fname}__seg{i:05d}.wav')
            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 1000:
                try:
                    wav, sr = sf.read(chunk_path)
                    chapter_wavs.append((i, wav))
                except Exception:
                    pending.append((i, seg))
            else:
                pending.append((i, seg))

        print(f'   待合成 {len(pending)}/{len(segs)} 段', flush=True)
        # 分批处理
        for b_start in range(0, len(pending), BATCH_SIZE):
            batch = pending[b_start:b_start + BATCH_SIZE]
            idxs = [x[0] for x in batch]
            texts = [x[1] for x in batch]
            try:
                wavs, sr = model.generate_voice_clone(
                    text=texts, language=['Chinese'] * len(texts),
                    voice_clone_prompt=prompt, max_new_tokens=MAX_NEW_TOKENS,
                )
                for i_off, w in enumerate(wavs):
                    i = idxs[i_off]
                    chunk_path = os.path.join(CHUNK_DIR, f'{fname}__seg{i:05d}.wav')
                    sf.write(chunk_path, w, sr)
                    chapter_wavs.append((i, w))
                    d = len(w) / sr
                    total_audio_sec += d
                    if len(idxs) == 1 or i_off == len(wavs) - 1:
                        el = time.time() - grand_start
                        print(f'  [{title} seg{i+1}/{len(segs)}] bat{idxs[0]//BATCH_SIZE+1} {d:.1f}s | 累计 {total_audio_sec:.0f}s ({total_audio_sec/60:.1f}min) | 已跑 {el/60:.1f}min', flush=True)
            except Exception as e:
                print(f'!! batch seg{idxs} 失败: {e}', flush=True)
                torch.cuda.empty_cache()
                # 单段重试
                for i, seg in batch:
                    try:
                        wavs, sr = model.generate_voice_clone(
                            text=seg, language='Chinese', voice_clone_prompt=prompt,
                            max_new_tokens=MAX_NEW_TOKENS,
                        )
                        w = wavs[0]
                        chunk_path = os.path.join(CHUNK_DIR, f'{fname}__seg{i:05d}.wav')
                        sf.write(chunk_path, w, sr)
                        chapter_wavs.append((i, w))
                        total_audio_sec += len(w) / sr
                    except Exception as e2:
                        print(f'!! seg{i} 单段重试也失败: {e2}', flush=True)
                continue

        if not chapter_wavs:
            print(f'!! {title} 无任何输出', flush=True)
            continue

        # 按段序号排序拼接
        chapter_wavs.sort(key=lambda x: x[0])
        silence = np.zeros(int(SAMPLE_RATE * SEG_SILENCE_SEC), dtype=np.float32)
        parts = []
        for k, (_, w) in enumerate(chapter_wavs):
            if w.dtype != np.float32:
                w = w.astype(np.float32)
            parts.append(w)
            if k < len(chapter_wavs) - 1:
                parts.append(silence)
        full = np.concatenate(parts)
        wav_out = os.path.join(OUT_DIR, f'{fname}.wav')
        mp3_out = os.path.join(OUT_DIR, f'{fname}.mp3')
        sf.write(wav_out, full, SAMPLE_RATE)
        print(f'>> 已保存 {wav_out} ({len(full)/SAMPLE_RATE:.0f}s)', flush=True)
        subprocess.run([FFMPEG, '-y', '-i', wav_out, '-codec:a', 'libmp3lame', '-qscale:a', '2',
                        mp3_out], check=False, capture_output=True)
        print(f'>> 已转码 {mp3_out}', flush=True)

    el = time.time() - grand_start
    print(f'== 全部完成: 总音频 {total_audio_sec:.0f}s ({total_audio_sec/60:.1f}min), 耗时 {el/60:.1f}min ==', flush=True)


if __name__ == '__main__':
    main()