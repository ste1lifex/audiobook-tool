# -*- coding: utf-8 -*-
"""
验证两件事：
  1) 163.8s 超长段是不是"句内无句末标点"造成的（二级切分 bug）
  2) 按"说话人轮次"重分段的代价 —— 现在有多少段跨越了说话人边界
     （跨说话人的段**无法**分配给单一角色，这是多角色的硬前提）
"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402
import engine  # noqa: E402

IN = r'input'


def get_book(src_kw, vol):
    for f in os.listdir(IN):
        if src_kw in f and f.lower().endswith('.epub'):
            _, books = ebook.parse_books(os.path.join(IN, f))
            for b in books:
                if vol in b['title']:
                    return b
    return None


b = get_book('海伯利安四部曲', '海伯利安的隕落')
ch = b['chapters'][32]      # ch33（index 33 → 列表下标 32）
print(f"=== 1) ch33（{ch['title']}，{len(ch['text'])} 字）里超长段的成因 ===")
segs = engine.split_segments(ch['text'])
print(f'  当前切出 {len(segs)} 段，最长 {max(len(s) for s in segs)} 字')
print()
for i in (55, 56, 57, 58):
    if i >= len(segs):
        continue
    s = segs[i]
    n_end = len(re.findall(r'[。！？；…]', s))
    n_com = len(re.findall(r'[，、：]', s))
    print(f'  seg{i}: {len(s):>5} 字   句末标点 {n_end} 个   逗号类 {n_com} 个')
    print(f'         开头: {s[:60]}...')
    print(f'         结尾: ...{s[-60:]}')
    if n_end == 0:
        print(f'         >>> 段内没有任何句末标点 → 说明它是"一个超长句"，二级切分没生效')
    print()

print('=' * 76)
print('=== 2) 按"说话人轮次"分段的代价 ===')
print('   一个"说话人轮次" = 一段旁白 或 一段连续引号对话')
print('   跨轮次的段无法分配给单一角色 → 多角色必须先重分段')
print()
QUOTE = [('「', '」'), ('“', '”'), ('『', '』')]


def turns(text):
    """把文本切成说话人轮次：引号外=旁白，引号内=对话，交替。"""
    out, pos = [], 0
    for a, bb in QUOTE:
        pass
    # 用统一正则找所有引号内容
    pat = re.compile('|'.join(re.escape(a) + r'[^' + re.escape(a) + re.escape(bb) + r']*' + re.escape(bb)
                              for a, bb in QUOTE))
    for m in pat.finditer(text):
        if m.start() > pos:
            out.append(('旁白', text[pos:m.start()]))
        out.append(('对话', m.group(0)))
        pos = m.end()
    if pos < len(text):
        out.append(('旁白', text[pos:]))
    return out


for label, book in (('海伯利安的隕落', b),):
    tot_seg = tot_mixed = 0
    turn_lens = []
    for c in book['chapters']:
        ts = turns(c['text'])
        for _, t in ts:
            turn_lens.append(len(t))
        segs = engine.split_segments(c['text'])
        for s in segs:
            tot_seg += 1
            # 该段含引号，且引号外还有实质文字 → 跨轮次
            has_q = any(a in s and bb in s for a, bb in QUOTE)
            outside = re.sub('|'.join(re.escape(a) + r'[^' + re.escape(a) + re.escape(bb) + r']*' + re.escape(bb)
                                      for a, bb in QUOTE), '', s)
            if has_q and len(outside.strip()) >= 10:
                tot_mixed += 1
    tl = sorted(turn_lens)
    print(f'  {label}:')
    print(f'    当前段数 {tot_seg:,}')
    print(f'    其中"文内既有对话又有旁白"（跨说话人）: {tot_mixed:,} ({tot_mixed/tot_seg:.1%})')
    print(f'    说话人轮次总数 {len(turn_lens):,}')
    print(f'    轮次长度: 中位 {tl[len(tl)//2]} 字, 均值 {sum(tl)/len(tl):.0f} 字, '
          f'P90 {tl[int(len(tl)*0.9)]} 字, 最长 {tl[-1]} 字')
    over = sum(1 for x in tl if x > 105)
    print(f'    轮次 >105 字的: {over:,} ({over/len(tl):.1%})  ← 这些轮次内部还需要按句/逗号再切')
    dia = sum(len(t) for k, t in [x for x in []])
    # 轮次里对话 vs 旁白占比
    kinds = Counter()
    for c in book['chapters']:
        for k, t in turns(c['text']):
            kinds[k] += len(t)
    tot = sum(kinds.values())
    print(f'    字数占比: 旁白 {kinds["旁白"]/tot:.1%}  对话 {kinds["对话"]/tot:.1%}')
