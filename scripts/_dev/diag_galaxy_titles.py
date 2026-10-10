# -*- coding: utf-8 -*-
"""深入看标题标签内容与 Contents 页，为修 ebook.py 提供依据。"""
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402
from text_clean import clean_text, extract_text_from_html  # noqa: E402

SRC = None
for f in os.listdir(r'input'):
    if '银河帝国' in f and f.lower().endswith('.epub'):
        SRC = os.path.join(r'input', f)
files, spine_paths, ncx_root = ebook._epub_open(SRC)


def first_heading(xhtml, levels=(1, 2, 3, 4, 5, 6)):
    for lv in levels:
        for m in re.finditer(rf'<h{lv}\b[^>]*>(.*?)</h{lv}>', xhtml, re.S | re.I):
            t = re.sub(r'<[^>]+>', '', m.group(1))
            t = re.sub(r'\s+', ' ', t).strip()
            if t:
                return f'h{lv}: {t[:34]}'
    return None


print('=== 前 20 个 spine 文档的首个标题标签 ===')
for zp in spine_paths[:20]:
    x = files.get(zp)
    if x is None:
        continue
    n = len(clean_text(extract_text_from_html(x)).replace('\n', ''))
    print(f'  {os.path.basename(zp):<20} {n:>7}字  {first_heading(x) or "（无标题标签）"}')

print('\n=== 所有 <title>Contents</title> 的文件及其字数 ===')
cn = []
for zp in spine_paths:
    x = files.get(zp)
    if x is None:
        continue
    m = re.search(r'<title[^>]*>(.*?)</title>', x, re.S | re.I)
    t = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''
    if t.lower() == 'contents':
        n = len(clean_text(extract_text_from_html(x)).replace('\n', ''))
        cn.append((os.path.basename(zp), n))
for nm, n in cn:
    print(f'  {nm:<20} {n:>6} 字')
print(f'  合计 {len(cn)} 个；其中字数 >=300 的（现有过滤器不会丢）: '
      f'{[(nm, n) for nm, n in cn if n >= 300]}')

print('\n=== 取一个 Contents 页看内容长相 ===')
for zp in spine_paths:
    x = files.get(zp)
    if x is None:
        continue
    m = re.search(r'<title[^>]*>(.*?)</title>', x, re.S | re.I)
    if m and re.sub(r'<[^>]+>', '', m.group(1)).strip().lower() == 'contents':
        txt = clean_text(extract_text_from_html(x))
        if len(txt.replace('\n', '')) >= 300:
            print(f'  文件: {zp}')
            print('  ' + txt[:300].replace('\n', ' / '))
            break

print('\n=== h2 的 15 个内容（应与 NCX 的 15 册对应）===')
h2 = []
for zp in spine_paths:
    x = files.get(zp)
    if x is None:
        continue
    for m in re.finditer(r'<h2\b[^>]*>(.*?)</h2>', x, re.S | re.I):
        t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', m.group(1))).strip()
        if t:
            h2.append((os.path.basename(zp), t[:40]))
for nm, t in h2:
    print(f'  {nm:<20} {t}')
