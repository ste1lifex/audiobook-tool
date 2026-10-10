# -*- coding: utf-8 -*-
"""
修复异常段：重新生成撞 token 上限的段（127.9s 循环段）。
- 用与 batch_synth 相同的 split_segments 重建各段文本
- 对时长 >60s 的段，用原文重新生成（最多 3 次）；仍超长则拆小重试
- 替换 chunk 文件，重新拼接全书 wav + mp3
"""
import os, glob, subprocess, re
import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MODEL_DIR = os.path.join(ROOT, r"Qwen3-TTS\pretrained_models\Qwen3-TTS-12Hz-0.6B-Base")
REF_AUDIO = os.path.join(ROOT, r"reference_audio\ref_clip_full.wav")
REF_TEXT = ""
SIMPLIFIED = os.path.join(ROOT, r"epub_extract\simplified")
OUT_DIR = os.path.join(ROOT, r"output\audiobook")
CHUNK_DIR = os.path.join(OUT_DIR, "_chunks")
SR = 24000
SIL = 0.30
MAX_CHARS = 205
BAD = 60.0


def split_segments(text, max_chars=MAX_CHARS):
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


def main():
    use_cuda = torch.cuda.is_available()
    print(f'CUDA: {use_cuda}', flush=True)
    model = Qwen3TTSModel.from_pretrained(
        MODEL_DIR, device_map='cuda:0' if use_cuda else 'cpu',
        dtype=torch.bfloat16 if use_cuda else torch.float32)
    prompt = model.create_voice_clone_prompt(
        ref_audio=REF_AUDIO, ref_text=REF_TEXT, x_vector_only_mode=False)
    print('>> 模型就绪', flush=True)

    chapters = [
        ('book1_part0006.txt', '第一章'),
        ('book1_part0007.txt', '第二章'),
    ]

    for fname, title in chapters:
        src = os.path.join(SIMPLIFIED, fname)
        if not os.path.exists(src):
            continue
        with open(src, encoding='utf-8') as f:
            text = f.read()
        segs = split_segments(text)
        # 目录下所有该章 chunk
        chunk_map = {}
        for c in glob.glob(os.path.join(CHUNK_DIR, f'{fname}__seg*.wav')):
            m = re.search(r'__seg(\d+)\.wav$', c)
            if m:
                chunk_map[int(m.group(1))] = c
        print(f'== {title}: {len(segs)} 段, chunk {len(chunk_map)} ==', flush=True)

        fixed = 0
        for idx, seg in enumerate(segs):
            cpath = chunk_map.get(idx)
            if not cpath or not os.path.exists(cpath):
                continue
            w, _ = sf.read(cpath)
            dur = len(w) / SR
            if dur <= BAD:
                continue

            print(f'  修复 seg{idx:05d} ({len(seg)}字, 原{dur:.0f}s) ...', flush=True)
            todo = [seg]
            new_wav = None
            for attempt in range(3):
                out_pieces = []
                ok_all = True
                for piece in todo:
                    piece_ok = False
                    for trial in range(2):
                        wavs, sr = model.generate_voice_clone(
                            text=piece, language='Chinese', voice_clone_prompt=prompt,
                            max_new_tokens=1600,
                        )
                        pw = wavs[0]
                        pd = len(pw) / sr
                        if pd <= BAD:
                            out_pieces.append(pw)
                            piece_ok = True
                            break
                    if not piece_ok:
                        # 拆小再试（仅本 piece）
                        sub = [s for s in re.split(r'(?<=[，；、])', piece) if s.strip()]
                        if len(sub) > 1 and len(piece) > 40:
                            todo = sub
                            ok_all = False
                            break
                        else:
                            # 最后手段：截断到 45s
                            out_pieces.append(pw[: int(SR * 45)])
                            piece_ok = True
                if ok_all and out_pieces:
                    if len(out_pieces) > 1:
                        sil = np.zeros(int(SR * 0.2), dtype=np.float32)
                        parts = []
                        for k, p in enumerate(out_pieces):
                            parts.append(p)
                            if k < len(out_pieces) - 1:
                                parts.append(sil)
                        new_wav = np.concatenate(parts)
                    else:
                        new_wav = out_pieces[0]
                    break
            if new_wav is not None:
                sf.write(cpath, new_wav, SR)
                print(f'    -> {len(new_wav)/SR:.1f}s', flush=True)
                fixed += 1
            else:
                print(f'    !! 失败', flush=True)

        print(f'{title} 共修复 {fixed} 段', flush=True)

        # 重新拼接全书
        sorted_c = sorted(chunk_map.items())
        if not sorted_c:
            continue
        parts = []
        sil = np.zeros(int(SR * SIL), dtype=np.float32)
        for i, (idx, cpath) in enumerate(sorted_c):
            w, _ = sf.read(cpath)
            if w.dtype != np.float32:
                w = w.astype(np.float32)
            parts.append(w)
            if i < len(sorted_c) - 1:
                parts.append(sil)
        full = np.concatenate(parts)
        wav_out = os.path.join(OUT_DIR, f'{fname}.wav')
        mp3_out = os.path.join(OUT_DIR, f'{fname}.mp3')
        sf.write(wav_out, full, SR)
        print(f'>> {wav_out} ({len(full)/SR/60:.1f} 分钟)', flush=True)
        subprocess.run([r"bin\ffmpeg.exe", "-y", "-i", wav_out,
                        "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_out],
                       check=True, capture_output=True)
        print(f'>> {mp3_out}', flush=True)

    print('== 全部修复完成 ==', flush=True)


if __name__ == '__main__':
    main()