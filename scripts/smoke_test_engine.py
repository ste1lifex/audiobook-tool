# -*- coding: utf-8 -*-
"""冒烟测试：用两小段文本跑通改过的 engine.synthesize_book（含采样参数与质检门）。"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import engine  # noqa: E402

BOOK = os.path.join(config.OUTPUT_BASE, '_smoketest')
if os.path.isdir(BOOK):
    shutil.rmtree(BOOK)
os.makedirs(BOOK)

t1 = ('她站在舷窗前，看着远处星球上那片缓慢流动的极光。'
      '她想起很多年前，自己第一次离开家乡的时候，也是这样望着窗外。')
t2 = ('先生，今天的行程已经排好了。如果你需要休息，我可以把剩下的会议推迟到明天。')

chapters = [
    {'index': 1, 'title': '测试一', 'text': t1},
    {'index': 2, 'title': '测试二', 'text': t2},
]
print('段数:', [len(engine.split_segments(c['text'])) for c in chapters], flush=True)
print('采样参数:', engine._gen_kwargs(), flush=True)
print('参考音频:', config.DEFAULT_REF_AUDIO, flush=True)

mp3, st = engine.synthesize_book(BOOK, chapters, book_title='冒烟测试')
print('\n=== 结果 ===')
print('status:', st['status'])
print('mp3:', mp3)
if mp3 and os.path.exists(mp3):
    print('大小: %.1f MB' % (os.path.getsize(mp3) / 1024 / 1024))
for c in st['chapters']:
    print('  ', c['index'], c['title'], 'done=', c['done'], c.get('mp3'))
print('\n产物目录:')
for root, _, files in os.walk(BOOK):
    for f in sorted(files):
        p = os.path.join(root, f)
        print(f'  {os.path.relpath(p, BOOK):<52} {os.path.getsize(p)/1024:8.1f} KB')
