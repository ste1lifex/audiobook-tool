# -*- coding: utf-8 -*-
"""
听感验证包：把"一致性离群"的段放进它原本的上下文里，
让你直接听出来那个段是不是真的"跳出去"了。

设计（避免让你听十几个孤立文件）：
  组 A：一致性最差的段 —— 假设 = 这些就是听到的"偏离"
        每段输出 [前2段] + [本段] + [后1段]，段间 0.45s 静音，
        文件名标注 ★ 在第几个，方便直接跳到它
  组 B：锚点最低但一致性正常的段 —— 假设 = 这些听着没问题
        如果组 B 听着也没问题，说明绝对门限口径确实在误导

用法: python make_consistency_check.py
输出: tts_eval\audio\_drift_check\
"""
import glob
import json
import os
import re
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

BOOK_DIR = config.OUTPUT_BASE + r'\海伯利安的隕落'
CHUNK_DIR = os.path.join(BOOK_DIR, '_chunks')
SAFE = '海伯利安的隕落'
OUT = r'tts_eval\audio\_drift_check'

# 来自 consistency_audit.py 的结果（ch1-3, n=227）
GROUP_A = [  # 一致性最差（门放过的）
    (1, 57), (1, 54), (2, 75), (3, 24), (2, 71), (3, 70), (2, 14), (1, 29),
]
GROUP_B = [  # 锚点最低但一致性正常
    (3, 25), (3, 0), (3, 19), (1, 8), (3, 23), (2, 40),
]


def seg_path(ch, seg):
    return os.path.join(CHUNK_DIR, f'{SAFE}_ch{ch:02d}_seg{seg:05d}.wav')


def load(path):
    if not os.path.exists(path):
        return None
    w, sr = sf.read(path)
    if w.ndim > 1:
        w = w.mean(axis=1)
    return w.astype(np.float32), sr


def build(items, tag):
    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for n, (ch, seg) in enumerate(items, 1):
        parts, marks = [], []
        for off in (-2, -1, 0, 1):
            p = seg_path(ch, seg + off)
            r = load(p)
            if r is None:
                continue
            w, sr = r
            marks.append((len(parts), off))
            parts.append(w)
        if not parts:
            continue
        sil = np.zeros(int(sr * 0.45), dtype=np.float32)
        out = []
        for i, w in enumerate(parts):
            if i:
                out.append(sil)
            out.append(w)
        audio = np.concatenate(out)
        peak = float(np.max(np.abs(audio))) or 1.0
        audio = (audio / peak * 0.95).astype(np.float32)
        star = next(i for i, off in marks if off == 0)
        # 计算 ★ 段开始的秒数
        idx = 0
        t = 0.0
        for i, w in enumerate(parts):
            if i == star:
                break
            t += len(w) / sr + 0.45
        name = f'{tag}_{n:02d}_ch{ch}_seg{seg:05d}_星在{t:.0f}s.wav'
        dst = os.path.join(OUT, name)
        sf.write(dst, audio, sr)
        dur = len(audio) / sr
        manifest.append(dict(file=name, ch=ch, seg=seg, star_at=round(t, 1),
                             total=round(dur, 1)))
        print(f'  {name}  总长 {dur:.1f}s  ★ 在第 {t:.0f}s')

    print(f'\n组 {tag} 共 {len(manifest)} 个文件 -> {OUT}')
    return manifest


print('=== 组 A：一致性最差的段（假设=你听到的偏离）===')
a = build(GROUP_A, 'A一致性最差')
print('\n=== 组 B：锚点最低但一致性正常（假设=听着没问题）===')
b = build(GROUP_B, 'B锚点最低')

with open(os.path.join(OUT, '_manifest.json'), 'w', encoding='utf-8') as f:
    json.dump(dict(group_a=a, group_b=b), f, ensure_ascii=False, indent=1)

print(f'''
听法建议：
  1. 先听组 A 的头三个。每个文件里 ★ 那个段是"统计上离群"的，
     看你能不能在那个位置听出"跳出去/不是同一个人"。
  2. 再听组 B。这些段"最不像参考音频"，但和邻居一致。
     如果组 B 听着也正常 → 说明绝对门限确实在误导，一致性才是对的判据。
  3. 告诉我：A 里几个有问题？B 里几个有问题？
     以及 ★ 出现的位置对不对（文件名里写了秒数）。
''')
