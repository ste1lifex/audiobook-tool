# -*- coding: utf-8 -*-
"""诊断《银河帝国》EPUB 的章节标题为何变成 Unknown。"""
import os
import re
import sys
import zipfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402
from text_clean import clean_text, extract_text_from_html  # noqa: E402

SRC = None
for f in os.listdir(r'input'):
    if '银河帝国' in f and f.lower().endswith('.epub'):
        SRC = os.path.join(r'input', f)
print('文件:', os.path.basename(SRC)[:50], '\n')

files, spine_paths, ncx_root = ebook._epub_open(SRC)
print(f'=== 概况 ===')
print(f'  xhtml/html 文件数: {len(files)}')
print(f'  spine 文档数:      {len(spine_paths)}')
print(f'  spine 前 5: {spine_paths[:5]}')
print(f'  NCX 是否解析成功:  {ncx_root is not None}')

# 1) <title> 标签统计
print(f'\n=== ① 每个 xhtml 的 <title> 值分布 ===')
titles = Counter()
no_title = 0
for zp in spine_paths:
    x = files.get(zp)
    if x is None:
        continue
    m = re.search(r'<title[^>]*>(.*?)</title>', x, re.S | re.I)
    if m:
        titles[re.sub(r'<[^>]+>', '', m.group(1)).strip()] += 1
    else:
        no_title += 1
for t, n in titles.most_common(10):
    print(f'    {n:>5} 个文件  <title>{t}</title>')
if no_title:
    print(f'    {no_title:>5} 个文件  无 <title> 标签')

# 2) 标题标签统计
print(f'\n=== ② 正文里有哪些标题标签 ===')
hd = Counter()
for zp in spine_paths:
    x = files.get(zp)
    if x is None:
        continue
    for lv in re.findall(r'<h([1-6])\b', x, re.I):
        hd[f'h{lv}'] += 1
print(f'    {dict(hd) if hd else "完全没有 h1~h6 标签"}')

# 3) NCX 结构
print(f'\n=== ③ NCX 目录结构 ===')
if ncx_root is not None:
    NS = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    navmap = ncx_root.find('n:navMap', NS)
    tops = navmap.findall('n:navPoint', NS) if navmap is not None else []
    print(f'    顶层 navPoint 数: {len(tops)}')
    nested = sum(1 for t in tops if t.findall('n:navPoint', NS))
    print(f'    其中含子节点的:   {nested}')

    def title_of(n):
        lab = n.find('n:navLabel/n:text', NS)
        return (lab.text or '').strip() if lab is not None else ''
    def src_of(n):
        c = n.find('n:content', NS)
        return c.get('src') if c is not None else None
    print(f'    前 12 个顶层项:')
    for t in tops[:12]:
        k = t.findall('n:navPoint', NS)
        print(f'      [{title_of(t)[:26]:<26}] 子节点 {len(k):>2}  src={str(src_of(t))[:44]}')

# 4) 试跑 _guess_title 看返回什么
print(f'\n=== ④ 现有 _guess_title 的实际返回（前 8 个 spine）===')
for zp in spine_paths[:8]:
    x = files.get(zp)
    if x is None:
        continue
    print(f'    {os.path.basename(zp):<22} -> {ebook._guess_title(x, os.path.basename(zp))!r}')

# 5) 总量与 Contents 页
print(f'\n=== ⑤ 规模 ===')
tot = 0
for zp in spine_paths:
    x = files.get(zp)
    if x:
        tot += len(clean_text(extract_text_from_html(x)).replace('\n', ''))
print(f'    全书正文合计: {tot:,} 字')
print(f'    （105 字分段约 {tot // 89:,} 段）')

print(f'\n=== ⑥ 第 1 章 Contents 的内容 ===')
print(repr(clean_text(extract_text_from_html(files[spine_paths[0]]))[:400]))
