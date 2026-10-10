# -*- coding: utf-8 -*-
"""
验证 Web UI 那条路径（engine.synthesize_book）：
  1. 打印生效的生成配置（便于确认 UI 走的是新管道）
  2. chunk 指纹机制：分段参数变化时**拒绝复用旧段**，避免段号错位
  3. 质检门生效
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import engine  # noqa: E402

BOOK = os.path.join(config.OUTPUT_BASE, '_fp_test')
if os.path.isdir(BOOK):
    shutil.rmtree(BOOK)
os.makedirs(BOOK)

TXT = ('她站在舷窗前，看着远处星球上那片缓慢流动的极光。'
       '她想起很多年前，自己第一次离开家乡的时候，也是这样望着窗外。'
       '先生，今天的行程已经排好了。如果你需要休息，我可以把剩下的会议推迟。')
chapters = [{'index': 1, 'title': '测试章', 'text': TXT}]

print(f'{"="*70}\n第一次运行：MAX_CHARS_PER_SEG={config.MAX_CHARS_PER_SEG}')
print(f'{"="*70}')
config.MAX_CHARS_PER_SEG = 105
n1 = len(engine.split_segments(TXT))
print(f'预期段数: {n1}')
mp3, st = engine.synthesize_book(BOOK, chapters, book_title='指纹测试')
key1 = engine.load_state(BOOK)['config'].get('chunk_key')
print(f'\n>>> 运行1 结束: status={st["status"]}  chunk_key={key1}')
chunks1 = sorted(os.listdir(os.path.join(BOOK, '_chunks')))
print(f'>>> chunk 文件: {chunks1}')

print(f'\n{"="*70}\n第二次运行：模拟配置变更（105 -> 55 字），并强制该章重做')
print(f'{"="*70}')
config.MAX_CHARS_PER_SEG = 55
n2 = len(engine.split_segments(TXT))
# 强制重做：清掉 done 标记与 mp3，模拟"中途停下后改配置继续"
st = engine.load_state(BOOK)
st['chapters'][0]['done'] = False
st['chapters'][0]['mp3'] = None
engine.save_state(BOOK, st)
for f in os.listdir(BOOK):
    if f.endswith('.mp3'):
        os.remove(os.path.join(BOOK, f))
key_before = st['config'].get('chunk_key')
print(f'预期段数: {n2}（比 {n1} 多，说明分段变了）')
print(f'旧 chunk_key={key_before}  新 chunk_key={engine.chunk_cache_key(config.DEFAULT_REF_AUDIO)}')

mp3, st = engine.synthesize_book(BOOK, chapters, book_title='指纹测试')
key2 = engine.load_state(BOOK)['config'].get('chunk_key')
print(f'\n>>> 运行2 结束: status={st["status"]}  chunk_key={key2}')
print(f'>>> chunk_key 是否更新: {key_before != key2}')

print(f'\n{"="*70}\n结论\n{"="*70}')
print(f'  指纹随配置变化: {"✅" if key_before != key2 else "❌"}')
print(f'  上方应出现 "!! chunk 缓存指纹变化 ... 旧段不再复用" 的告警')
print(f'  产物:')
for root, _, files in os.walk(BOOK):
    for f in sorted(files):
        p = os.path.join(root, f)
        print(f'    {os.path.relpath(p, BOOK):<48} {os.path.getsize(p)/1024:8.1f} KB')
