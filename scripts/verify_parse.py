# -*- coding: utf-8 -*-
"""验证 ebook.py 的修改：新书（银河帝国）要修好，老书（海伯利安四部曲）不能退化。"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ebook  # noqa: E402

IN = r'input'
FILES = [f for f in os.listdir(IN) if f.lower().endswith('.epub')]

for fn in FILES:
    path = os.path.join(IN, fn)
    print('=' * 78)
    print(f'文件: {fn[:60]}')
    name, books = ebook.parse_books(path)
    print(f'  合集名: {name[:50]}')
    print(f'  识别出 {len(books)} 册, 合计 {sum(len(b["chapters"]) for b in books)} 章, '
          f'{sum(len(c["text"]) for b in books for c in b["chapters"]):,} 字')
    for b in books:
        tot = sum(len(c["text"]) for c in b["chapters"])
        print(f'    - {b["title"][:34]:<36} {len(b["chapters"]):>4} 章  {tot:>9,} 字')
    # 标题质量检查
    allt = [c['title'] for b in books for c in b['chapters']]
    bad = [t for t in allt if t.strip().lower() in ('unknown', 'untitled', 'contents', '目录')
           or t.strip() == '']
    print(f'  标题质量: 总 {len(allt)} 个, 异常(Unknown/Contents/空) {len(bad)} 个 '
          f'{"✅" if not bad else "❌ " + str(bad[:5])}')
    print(f'  标题样例(第2册前6章):')
    if len(books) > 1:
        for c in books[1]['chapters'][:6]:
            print(f'      [{c["title"][:32]:<34}] {len(c["text"]):>6} 字')
