# -*- coding: utf-8 -*-
"""
生成三路引擎共用的测试段落集。

公平性设计：
  LongCat-AudioDiT 单次生成硬上限 max_wav_duration=30s，且要扣掉参考音频占用时长
  （ref_tight 6.08s → 预算约 24s），而它语速是写死的 0.21 秒/中文字
  → 单次最多约 113 字。为了让三路在同一批文本上公平比较，段长上限取 105 字。

输出 segments.json：
  [{ "idx":0, "text":"...", "chars":N, "src_seg":<原第六章段号> }, ...]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import engine  # noqa: E402

OUT = r'tts_eval\scripts\segments.json'
CH_INDEX = 7                 # 第六章
MAX_CHARS = 105              # LongCat 约束下的安全上限
N_SEG = 12                   # 取连续 12 段

book_dir = os.path.join(config.OUTPUT_BASE, '海伯利安')
with open(os.path.join(book_dir, '_chapters.json'), encoding='utf-8') as f:
    chapters = json.load(f)
ch = [c for c in chapters if c['index'] == CH_INDEX][0]

# 用项目自己的分句逻辑按 105 字上限重切（不能手工截断，会丢字）
segs205 = engine.split_segments(ch['text'])                      # 项目当前口径
merged = engine.split_segments(ch['text'], max_chars=MAX_CHARS)  # 评测口径

print(f'第六章原文 {len(ch["text"])} 字')
print(f'项目当前分段(205字上限): {len(segs205)} 段')
print(f'本次评测分段(105字上限): {len(merged)} 段  '
      f'(平均 {sum(len(s) for s in merged)/len(merged):.0f} 字)')
lost = len(ch['text'].replace('\n', '')) - sum(len(s) for s in merged)
print(f'重切后覆盖校验: 原文非空白 {len(ch["text"].replace(chr(10),""))} 字, '
      f'分段合计 {sum(len(s) for s in merged)} 字, 差 {lost} 字（标点/空白）')

# 取中段连续 N 段（避免开头/结尾的特殊内容）
start = max(0, len(merged) // 3)
picked = merged[start:start + N_SEG]
out = [dict(idx=i, text=t, chars=len(t), src_seg=start + i)
       for i, t in enumerate(picked)]
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

print(f'\n取连续 {len(out)} 段（起始于合并后的第 {start} 段）:')
for r in out:
    print(f'  [{r["idx"]:2d}] {r["chars"]:3d}字  {r["text"][:44]}...')
print(f'\n-> {OUT}')
