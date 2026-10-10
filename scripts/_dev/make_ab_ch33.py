# -*- coding: utf-8 -*-
"""
ch33 的 A/B：同一段文字，旧切分(v1) vs 新切分(v2)。

原理：v1 和 v2 都只是对同一段文本的不同 partition，
      所以按「字符偏移」可以精确对齐，结果是可比的。

输出到 tts_eval\audio\_ab_ch33\
  A_旧切分_v1.wav      原来坏掉的那段（含 163.8s 撞顶截断段 + 8 字垃圾段）
  B_新切分_v2.wav      同一段文字，新切分后的若干段
  另附 print 出的逐段清单
"""
import glob
import os
import re
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import engine  # noqa: E402

BOOK = '海伯利安的隕落'
BOOK_DIR = os.path.join(config.OUTPUT_BASE, BOOK)
OLD_DIR = os.path.join(BOOK_DIR, '_chunks')
NEW_DIR = os.path.join(BOOK_DIR, '_chunks_v2seg')
OUT = r'tts_eval\audio\_ab_ch33'
CH = 33
PAD = 1          # 对比区间前后各留几段做上下文


def split_v1(text, max_chars=105):
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


def offsets(segs):
    out, o = [], 0
    for i, s in enumerate(segs):
        out.append((i, o, o + len(s), s))
        o += len(s)
    return out


def wav_path(d, i):
    return os.path.join(d, f'{BOOK}_ch{CH:02d}_seg{i:05d}.wav')


def load_concat(idxs, d, sil=0.45):
    parts, sr = [], None
    for i in idxs:
        p = wav_path(d, i)
        if not os.path.exists(p):
            return None, None
        w, sr = sf.read(p)
        if w.ndim > 1:
            w = w.mean(axis=1)
        parts.append(w.astype(np.float32))
    if not parts:
        return None, None
    gap = np.zeros(int(sr * sil), dtype=np.float32)
    seq = []
    for i, w in enumerate(parts):
        if i:
            seq.append(gap)
        seq.append(w)
    a = np.concatenate(seq)
    return a / (float(np.max(np.abs(a))) or 1.0) * 0.95, sr


def main():
    with open(os.path.join(BOOK_DIR, '_chapters.json'), encoding='utf-8') as f:
        import json
        chapters = json.load(f)
    text = next(c for c in chapters if c['index'] == CH)['text']

    v1 = split_v1(text)
    v2 = engine.split_segments(text)
    o1, o2 = offsets(v1), offsets(v2)
    print(f'ch33 文本 {len(text)} 字（去空白后 {len(re.sub(chr(92)+"s+","",text))}）')
    print(f'  v1: {len(v1)} 段, 最长 {max(len(x) for x in v1)} 字')
    print(f'  v2: {len(v2)} 段, 最长 {max(len(x) for x in v2)} 字')

    # 旧切分里 >105 字的段 = 出问题的段；聚焦最大的那一段（其余只是轻微超长）
    bad = [i for i, s in enumerate(v1) if len(s) > 105]
    print(f'\n旧切分里超过 105 字的段: {[(i, len(v1[i])) for i in bad]}')
    if not bad:
        print('没有超长段，退出')
        return
    tgt = max(bad, key=lambda i: len(v1[i]))          # 最长的那个 = 最坏
    lo = max(0, tgt - PAD)
    hi = min(len(v1) - 1, tgt + PAD)
    print(f'\n最坏的段: v1 seg{tgt}（{len(v1[tgt])} 字）')
    print(f'A/B 对比区间: 旧 v1 seg{lo}..seg{hi}（前后各留 {PAD} 段做上下文）')

    # 字符区间
    c_lo = o1[lo][1]
    c_hi = o1[hi][2]
    print(f'  对应字符偏移 {c_lo} .. {c_hi}  ({c_hi-c_lo} 字)')

    # 找到 v2 里覆盖该区间的最小段范围
    v2_idx = [i for i, a, b, s in o2 if b > c_lo and a < c_hi]
    print(f'  新 v2 覆盖段: seg{min(v2_idx)}..seg{max(v2_idx)}  共 {len(v2_idx)} 段')

    print(f'\n  旧切分各段:')
    for i in range(lo, hi + 1):
        p = wav_path(OLD_DIR, i)
        dur = None
        if os.path.exists(p):
            w, sr = sf.read(p)
            dur = len(w) / sr
        flag = '  <<< 超长段' if len(v1[i]) > 105 else ''
        print(f'    seg{i:>3}  {len(v1[i]):>5} 字  {("%.1fs" % dur) if dur else "???":>8}{flag}')
        print(f'           {v1[i][:70]}')

    print(f'\n  新切分各段（前 8 个）:')
    for i in v2_idx[:8]:
        p = wav_path(NEW_DIR, i)
        dur = None
        if os.path.exists(p):
            w, sr = sf.read(p)
            dur = len(w) / sr
        print(f'    seg{i:>3}  {len(v2[i]):>5} 字  {("%.1fs" % dur) if dur else "待生成":>8}   {v2[i][:60]}')

    # 缺文件的话先不生成音频
    miss_a = [i for i in range(lo, hi + 1) if not os.path.exists(wav_path(OLD_DIR, i))]
    miss_b = [i for i in v2_idx if not os.path.exists(wav_path(NEW_DIR, i))]
    if miss_b:
        print(f'\n新切分还缺 {len(miss_b)} 段（生成中），稍后再来。')
        return
    if miss_a:
        print(f'\n旧切分缺 {miss_a}')
        return

    os.makedirs(OUT, exist_ok=True)
    a, sr = load_concat(list(range(lo, hi + 1)), OLD_DIR)
    b, _ = load_concat(v2_idx, NEW_DIR)
    fa = os.path.join(OUT, 'A_旧切分_v1.wav')
    fb = os.path.join(OUT, 'B_新切分_v2.wav')
    sf.write(fa, a, sr)
    sf.write(fb, b, sr)
    print(f'\n=== 生成完毕 ===')
    print(f'  A 旧切分: {fa}  {len(a)/sr:.1f}s')
    print(f'  B 新切分: {fb}  {len(b)/sr:.1f}s')
    print(f'  两者文字相同（{c_hi-c_lo} 字），可直接对比听感')


if __name__ == '__main__':
    main()
