# -*- coding: utf-8 -*-
"""
验证 split_segments v2：
  1) 超长段是否结构性消失（v1 最长 1301 字）
  2) 普通章节是否几乎不受影响（段数变化应很小）
  3) ch33 那两段咏唱体现在被切成什么样
  4) 段缓存指纹是否变化（防止旧段被静默复用）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import ebook   # noqa: E402
import engine  # noqa: E402


def split_v1(text, max_chars=105):
    """原来的实现（用于对照）。"""
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


IN = r'input'
b = None
for f in os.listdir(IN):
    if '海伯利安四部曲' in f and f.lower().endswith('.epub'):
        _, books = ebook.parse_books(os.path.join(IN, f))
        b = [x for x in books if '海伯利安的隕落' in x['title']][0]
print(f"=== {b['title']}  {len(b['chapters'])} 章  "
      f"{sum(len(c['text']) for c in b['chapters']):,} 字 ===\n")

n1 = n2 = 0
max1 = max2 = 0
over1 = over2 = 0
changed = []
for c in b['chapters']:
    s1 = split_v1(c['text'])
    s2 = engine.split_segments(c['text'])
    n1 += len(s1); n2 += len(s2)
    m1 = max(len(x) for x in s1) if s1 else 0
    m2 = max(len(x) for x in s2) if s2 else 0
    max1 = max(max1, m1); max2 = max(max2, m2)
    over1 += sum(1 for x in s1 if len(x) > 105)
    over2 += sum(1 for x in s2 if len(x) > 105)
    if len(s1) != len(s2):
        changed.append((c['index'], c['title'], len(s1), len(s2), m1, m2))

print('=== 1) 总体对比 ===')
print(f'  {"":<16}{"v1(原)":>10}{"v2(新)":>10}{"":>4}')
print(f'  {"总段数":<16}{n1:>10,}{n2:>10,}   差 {n2-n1:+,} ({(n2-n1)/n1:+.2%})')
print(f'  {"最长段(字)":<16}{max1:>10,}{max2:>10,}')
print(f'  {"超过105字的段":<16}{over1:>10,}{over2:>10,}   ← 必须为 0')
print()
print('=== 2) 段数有变化的章 ===')
if not changed:
    print('  （无）')
for ch, t, a, bb, m1, m2 in changed:
    print(f'  ch{ch:02d} {t:<14} {a:>4} -> {bb:>4} 段   (最长 {m1} -> {m2} 字)')
print(f'  共 {len(changed)}/{len(b["chapters"])} 章段数变化')

print('\n=== 3) ch33 的咏唱体段现在怎么切 ===')
ch33 = [c for c in b['chapters'] if c['index'] == 33][0]
s1 = split_v1(ch33['text'])
s2 = engine.split_segments(ch33['text'])
print(f'  v1: {len(s1)} 段, 最长 {max(len(x) for x in s1)} 字')
print(f'  v2: {len(s2)} 段, 最长 {max(len(x) for x in s2)} 字')
big1 = [i for i, x in enumerate(s1) if len(x) > 105]
print(f'  v1 中超过 105 字的段: {[(i, len(s1[i])) for i in big1]}')
# 找出 v2 里对应原来那个 1301 字段的位置
i1301 = next((i for i, x in enumerate(s1) if len(x) > 1000), None)
if i1301 is not None:
    print(f'\n  —— 原来 s1[{i1301}]（{len(s1[i1301])} 字）被切成 {len(engine.split_segments(s1[i1301]))} 段：')
    for k, x in enumerate(engine.split_segments(s1[i1301])[:6], 1):
        print(f'      {k}. [{len(x):>3}字] {x[:64]}')
print(f'\n  —— v2 里 ch33 最长 6 段（应都 <=105）：')
for x in sorted(s2, key=len, reverse=True)[:6]:
    print(f'      [{len(x):>3}字] {x[:64]}')

print('\n=== 4) 段缓存指纹 ===')
import importlib
importlib.reload(config)
print(f'  SEG_VERSION = {config.SEG_VERSION}')
print(f'  新指纹 = {engine.chunk_cache_key(config.DEFAULT_REF_AUDIO)}')
print(f'  已生成的书里存的 = c105_ref_tight_6c82be  → 不一致则拒绝复用旧段 ✓')

print('\n=== 5) 抽查：普通章节首段（确认没切碎）===')
ch1 = [c for c in b['chapters'] if c['index'] == 1][0]
for i, x in enumerate(engine.split_segments(ch1['text'])[:5], 1):
    print(f'  {i}. [{len(x):>3}字] {x}')
