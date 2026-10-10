# -*- coding: utf-8 -*-
"""
多章节接力合成：一次加载模型，连续合成指定章节列表（默认第三章起）。
用法:
  envs\qwen3tts\Scripts\python.exe scripts\batch_synth_next.py [章节文件1,章节文件2,...]
  例: python scripts\batch_synth_next.py book1_part0008.txt,book1_part0009.txt,book1_part0010.txt
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

MAX_CHARS_PER_SEG = 205
BATCH_SIZE = 4
MAX_NEW_TOKENS = 1600
SEG_SILENCE_SEC = 0.30
SAMPLE_RATE = 24000
BAD_DUR = 60.0

DEFAULT_FILES = [
    'book1_part0008.txt', 'book1_part0009.txt', 'book1_part0010.txt',
    'book1_part0011.txt', 'book1_part0012.txt',
]


def split_segments(text, max_chars=MAX_CHARS_PER_SEG):
    text = re.sub(r'\s+', '', text)
    parts = re.split(r'(?<=[。！？；…])', text)
    segs, buf = [], ''
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


def ensure_chapter(path, title):
    """单个章节：断点续跑 + batch 合成 + 拼接。返回 (chunk_count, audio_sec)。"""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    segs = split_segments(text)
    fname = os.path.basename(path)
    print(f'== {title}: {len(text)} 字 -> {len(segs)} 段 ==', flush=True)

    # 已有 chunk
    chunk_map = {}
    for c in os.listdir(CHUNK_DIR):
        m = re.match(rf'^{fname}__seg(\d+)\.wav$', c)
        if m:
            chunk_map[int(m.group(1))] = os.path.join(CHUNK_DIR, c)

    chapter_wavs = {}
    pending = []
    for i, seg in enumerate(segs):
        if i in chunk_map:
            try:
                w, sr = sf.read(chunk_map[i])
                if len(w) / sr <= BAD_DUR:
                    chapter_wavs[i] = w
                    continue
            except Exception:
                pass
            # 异常或读取失败 -> 重新生成
        pending.append((i, seg))
    print(f'   待合成 {len(pending)}/{len(segs)} 段', flush=True)

    # 分批生成（与全局共享 model 由外部传入，这里用全局 vars）
    global _model, _prompt
    from synth_guard import generate_guarded, check_chunk_long, BAD_DUR as GUARD_BAD
    for b_start in range(0, len(pending), BATCH_SIZE):
        batch = pending[b_start:b_start + BATCH_SIZE]
        idxs = [x[0] for x in batch]
        texts = [x[1] for x in batch]
        try:
            wavs, sr = _model.generate_voice_clone(
                text=texts, language=['Chinese'] * len(texts),
                voice_clone_prompt=_prompt, max_new_tokens=MAX_NEW_TOKENS,
            )
            for i_off, w in enumerate(wavs):
                i = idxs[i_off]
                cpath = os.path.join(CHUNK_DIR, f'{fname}__seg{i:05d}.wav')
                # 时长校验：异常段立即用防护函数重生成
                dur = len(w) / sr
                if dur > GUARD_BAD:
                    print(f'  !! seg{i} batch 结果超长({dur:.0f}s)，走防护重生成', flush=True)
                    wg, srg = generate_guarded(_model, _prompt, texts[i_off],
                                               max_new_tokens=MAX_NEW_TOKENS)
                    if wg is not None:
                        w = wg
                        sr = srg
                    else:
                        print(f'  !! seg{i} 防护生成失败，保留原结果', flush=True)
                if w.dtype != np.float32:
                    w = w.astype(np.float32)
                sf.write(cpath, w, sr)
                chapter_wavs[i] = w
                if len(idxs) == 1 or i_off == len(wavs) - 1:
                    print(f'  [seg{i+1}/{len(segs)}] bat{idxs[0]//BATCH_SIZE+1} {len(w)/sr:.1f}s', flush=True)
        except Exception as e:
            print(f'!! batch seg{idxs} 失败: {e}', flush=True)
            torch.cuda.empty_cache()
            for i, seg in batch:
                wg, srg = generate_guarded(_model, _prompt, seg, max_new_tokens=MAX_NEW_TOKENS)
                if wg is None:
                    print(f'!! seg{i} 防护生成失败', flush=True)
                    continue
                cpath = os.path.join(CHUNK_DIR, f'{fname}__seg{i:05d}.wav')
                if wg.dtype != np.float32:
                    wg = wg.astype(np.float32)
                sf.write(cpath, wg, srg)
                chapter_wavs[i] = wg

    if not chapter_wavs:
        print(f'!! {title} 无输出', flush=True)
        return 0, 0.0

    # 拼接
    sorted_idx = sorted(chapter_wavs.keys())
    silence = np.zeros(int(SAMPLE_RATE * SEG_SILENCE_SEC), dtype=np.float32)
    parts = []
    for k, i in enumerate(sorted_idx):
        w = chapter_wavs[i]
        if w.dtype != np.float32:
            w = w.astype(np.float32)
        parts.append(w)
        if k < len(sorted_idx) - 1:
            parts.append(silence)
    full = np.concatenate(parts)
    wav_out = os.path.join(OUT_DIR, f'{fname}.wav')
    mp3_out = os.path.join(OUT_DIR, f'{fname}.mp3')
    sf.write(wav_out, full, SAMPLE_RATE)
    d = len(full) / SAMPLE_RATE
    print(f'>> {wav_out} ({d/60:.1f} 分钟)', flush=True)
    subprocess.run([FFMPEG, '-y', '-i', wav_out, '-codec:a', 'libmp3lame', '-qscale:a', '2',
                    mp3_out], check=False, capture_output=True)
    print(f'>> {mp3_out}', flush=True)
    return len(chapter_wavs), d


def main():
    global _model, _prompt
    files = DEFAULT_FILES
    if len(sys.argv) > 1:
        files = [x for x in sys.argv[1].split(',') if x]
    use_cuda = torch.cuda.is_available()
    print(f'CUDA: {use_cuda} | 待合成章节: {files}', flush=True)
    _model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR,
        device_map='cuda:0' if use_cuda else 'cpu',
        dtype=torch.bfloat16 if use_cuda else torch.float32)
    _prompt = _model.create_voice_clone_prompt(
        ref_audio=REF_AUDIO, ref_text=REF_TEXT, x_vector_only_mode=False)
    print('>> 模型就绪', flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CHUNK_DIR, exist_ok=True)

    t0 = time.time()
    total_dur = 0.0
    for fname in files:
        path = os.path.join(SIMPLIFIED, fname)
        if not os.path.exists(path):
            print(f'!! 缺 {path}', flush=True)
            continue
        title = f'{fname.split("_")[0]} {fname.split(".")[0][-4:]}'
        n, d = ensure_chapter(path, title)
        total_dur += d
        print(f'--- 已累计音频 {total_dur/60:.1f} 分钟, 运行 { (time.time()-t0)/60:.1f} 分钟 ---', flush=True)
    print(f'== 全部完成: 总音频 {total_dur/60:.1f} 分钟 ==', flush=True)


if __name__ == '__main__':
    main()