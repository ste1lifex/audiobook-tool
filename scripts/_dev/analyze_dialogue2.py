# -*- coding: utf-8 -*-
"""
更严谨地量：纯规则能把"这句对话是谁说的"覆盖到什么程度。
扩大动词表 + 加窗口（本段 + 上一段）后重新统计。
"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402

IN = r'input'

# 大幅扩充的说话动词（中文小说常见）
VERBS = ['说道', '说', '道', '问道', '问', '答道', '答', '回答', '回', '喊道', '叫道',
         '低声说', '低声', '喃喃', '嘟囔', '吼', '笑着说', '接话', '接着', '继续',
         '补充', '指出', '解释', '承认', '否认', '反问', '感叹', '低语', '嘀咕',
         '插嘴', '打断', '重复', '强调', '提醒', '警告', '宣布', '承认道', '咕哝',
         '自言自语', '告诉', '询问', '回应', '说道：', '说：', '道：']
VERB_RE = '|'.join(sorted(VERBS, key=len, reverse=True))

# 引号对
PAIRS = [('「', '」'), ('“', '”'), ('『', '』')]

# 明显的非人名（代词/连词/副词/常见动宾）
STOP = set('我 他 她 它 你 我们 他们 她们 你们 大家 有人 两人 双方 然后 于是 接着 后来 '
           '这时 此时 现在 当时 之后 之前 终于 突然 忽然 立刻 马上 已经 仍然 还是 只是 '
           '但是 可是 不过 因此 所以 因为 如果 虽然 然而 同时 另外 此外 其实 显然 当然 '
           '他继续 她又 他又 哈定插嘴 对方 那人 此人 那人 一个 这个 那个 什么 怎么'.split())


def get_text(src_keyword, vol_title):
    for f in os.listdir(IN):
        if src_keyword in f and f.lower().endswith('.epub'):
            _, books = ebook.parse_books(os.path.join(IN, f))
            for b in books:
                if vol_title in b['title']:
                    return '\n'.join(c['text'] for c in b['chapters'])
    return ''


BOOKS = [('海伯利安的隕落', '海伯利安四部曲', '海伯利安的隕落'),
         ('银河帝国1基地', '银河帝国', '银河帝国基地')]

for label, src_kw, vol in BOOKS:
    txt = get_text(src_kw, vol)
    if not txt:
        print(f'!! {label} 取不到')
        continue
    paras = [p.strip() for p in txt.split('\n') if p.strip()]
    total = len(txt)
    print('=' * 74)
    print(f'{label}   正文 {total:,} 字   段 {len(paras):,}')

    # 找到所有"含成对引号"的段
    dlg_idx = []
    for i, p in enumerate(paras):
        if any(a in p and b in p for a, b in PAIRS):
            dlg_idx.append(i)
    print(f'\n[1] 含对话的段: {len(dlg_idx):,}')

    # 归属来源：本段内 / 上一段
    both, only_prev, neither = 0, 0, 0
    roster = Counter()
    for i in dlg_idx:
        p = paras[i]
        prev = paras[i - 1] if i > 0 else ''
        in_cur = re.search(VERB_RE, p)
        # 本段的候选名（动词附近的中文串）
        cand = re.findall(r'([\u4e00-\u9fff]{2,4})(?:' + VERB_RE + r')', p)
        cand = [c for c in cand if c not in STOP]
        if in_cur and cand:
            both += 1
            roster.update(cand)
        elif re.search(r'([\u4e00-\u9fff]{2,4})(?:' + VERB_RE + r')', prev):
            only_prev += 1
            roster.update([c for c in re.findall(r'([\u4e00-\u9fff]{2,4})(?:' + VERB_RE + r')', prev)
                           if c not in STOP])
        else:
            neither += 1
    n = max(1, len(dlg_idx))
    print(f'\n[2] "谁在说话"的归属覆盖（纯规则，扩大动词表后）')
    print(f'    本段内就能定位  {both:>5}  ({both/n*100:5.1f}%)')
    print(f'    需看上一段      {only_prev:>5}  ({only_prev/n*100:5.1f}%)')
    print(f'    两段都找不到    {neither:>5}  ({neither/n*100:5.1f}%)  ← 这部分才真正需要 LLM')
    print(f'    规则可覆盖      {both+only_prev:>5}  ({(both+only_prev)/n*100:5.1f}%)')

    # 角色名册规模
    names = {k: v for k, v in roster.items() if 2 <= len(k) <= 4 and v >= 3}
    print(f'\n[3] 规则能自动攒出的角色名册（出现≥3次）: {len(names)} 个')
    top = sorted(names.items(), key=lambda x: -x[1])[:20]
    print('    前 20: ' + '、'.join(f'{k}({v})' for k, v in top))
    cum = 0
    allv = sum(names.values())
    for k, v in sorted(names.items(), key=lambda x: -x[1]):
        cum += v
        if cum / max(1, allv) >= 0.8:
            print(f'    覆盖 80% 归属只需 top {len([1 for kk,vv in names.items() if vv>=v])} 个角色')
            break

    # 纯"旁白 vs 对话"二分（完全不需要归属）
    covers = sum(len(m.group(0)) for a, b in PAIRS
                 for m in re.finditer(re.escape(a) + r'[^' + re.escape(a) + re.escape(b) + r']*' + re.escape(b), txt))
    print(f'\n[4] 零 LLM、零归属的"旁白/对话"二分就能覆盖: {covers:,} 字 ({covers/total*100:.1f}% 正文)')
