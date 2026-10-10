# -*- coding: utf-8 -*-
"""
分析用户自己的书里"对话"是怎么标注的，用来判断：
  多角色拆分到底需不需要 LLM，还是纯规则就够。
"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402

IN = r'input'
BOOKS = {
    '海伯利安的隕落': ('海伯利安四部曲', '海伯利安的隕落'),
    '银河帝国1基地': ('银河帝国', '银河帝国基地'),
}


def get_text(src_keyword, vol_title):
    for f in os.listdir(IN):
        if src_keyword in f and f.lower().endswith('.epub'):
            _, books = ebook.parse_books(os.path.join(IN, f))
            for b in books:
                if vol_title in b['title']:
                    return '\n'.join(c['text'] for c in b['chapters'])
    return ''


# 对话引号候选（中英文 + 繁体）
QUOTE_PAIRS = [('「', '」'), ('“', '”'), ('『', '』'), ('"', '"')]
# 说话动词（中文小说的高频归属词）
SAY_VERBS = ['说道', '说', '道', '问道', '问', '答道', '回答', '喊道', '叫道', '低声说',
             '喃喃', '嘟囔', '吼道', '笑着说', '接着', '继续']

for name, (src_kw, vol) in BOOKS.items():
    txt = get_text(src_kw, vol)
    if not txt:
        print(f'!! 取不到 {name} 的正文')
        continue
    total = len(txt)
    print('=' * 72)
    print(f'{name}   正文 {total:,} 字')
    # 引号统计
    print('\n[1] 引号使用情况')
    for a, b in QUOTE_PAIRS:
        cnt = txt.count(a)
        if cnt:
            # 成对才算
            span = sum(len(m.group(0)) for m in re.finditer(re.escape(a) + r'[^' + re.escape(a) + re.escape(b) + r']*' + re.escape(b), txt))
            print(f'    {a}{b}  左引号 {cnt:>6} 次   成对内容合计 {span:>8,} 字 '
                  f'({span/total*100:.1f}% 正文)')
    # 每段是否含对话
    paras = [p for p in txt.split('\n') if p.strip()]
    with_dlg = [p for p in paras if any(a in p for a, _ in QUOTE_PAIRS)]
    print(f'\n[2] 段落级  总段 {len(paras):,}   含对话段 {len(with_dlg):,} '
          f'({len(with_dlg)/max(1,len(paras))*100:.1f}%)')

    # 归属模式：引号后紧跟 XX说/道
    pat_after = re.compile(r'[」”』"]\s*([\u4e00-\u9fff]{1,6}?)(?:' + '|'.join(SAY_VERBS) + r')')
    m_after = pat_after.findall(txt)
    # 引号前 XX说/道
    pat_before = re.compile(r'([\u4e00-\u9fff]{1,6}?)(?:' + '|'.join(SAY_VERBS) + r')[：:]?\s*[「“『"]')
    m_before = pat_before.findall(txt)
    print(f'\n[3] 显式归属（"XX说/道"+引号）')
    print(f'    引号后置归属  {len(m_after):>6} 次')
    print(f'    引号前置归属  {len(m_before):>6} 次')
    names = Counter(m_after) + Counter(m_before)
    print(f'    提取到的"疑似人名" {len(names)} 个，高频前 12:')
    for n, c in names.most_common(12):
        print(f'       {n:<8} {c:>5} 次')
    # 只看像人名的（2-4 字，排除明显非人名）
    good = {n: c for n, c in names.items()
            if 2 <= len(n) <= 4 and not re.search(r'[的了是在有和就都也还很把被让给对从到与]', n)}
    print(f'    过滤后"像人名"的 {len(good)} 个，覆盖归属次数 '
          f'{sum(good.values()):,} / {len(m_after)+len(m_before):,}')
    print(f'    最高频 15: ' + '、'.join(f'{n}({c})' for n, c in
          sorted(good.items(), key=lambda x: -x[1])[:15]))
    # 无归属的对话段占比
    no_attr = [p for p in with_dlg
               if not re.search('|'.join(SAY_VERBS), p)]
    print(f'\n[4] 含对话但段内完全找不到说话动词的段: {len(no_attr):,} '
          f'（占对话段 {len(no_attr)/max(1,len(with_dlg))*100:.1f}%）')
    if no_attr:
        print('    样例:')
        for p in no_attr[:3]:
            print(f'      「{p.strip()[:70]}」')
