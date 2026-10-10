# -*- coding: utf-8 -*-
"""
测试多角色第 1 层：说话人轮次切分 + 规则归属 + 按音色合并。
重点看三个数：段数膨胀倍数、音色分布、归属覆盖率。
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config   # noqa: E402
import ebook    # noqa: E402
import engine   # noqa: E402
import speaker  # noqa: E402

IN = r'input'
b = None
for f in os.listdir(IN):
    if '海伯利安四部曲' in f and f.lower().endswith('.epub'):
        _, books = ebook.parse_books(os.path.join(IN, f))
        b = [x for x in books if '海伯利安的隕落' in x['title']][0]

print(f"=== 《{b['title']}》 {len(b['chapters'])} 章 ===\n")

# 用全书正文建名册（真实使用时也应该整本建，而不是逐章）
full = '\n'.join(c['text'] for c in b['chapters'])
roster = speaker.build_roster(full, min_count=3, top=8)
print(f'[1] 自动提取的主要角色（top 8）: {roster}')
print()

# 挑几章测试
TEST_CH = [1, 20, 33, 41]
print('[2] 逐章结果')
print(f"    {'章':>4}{'轮次':>7}{'音色组':>8}{'段数':>7}{'基线段数':>9}{'膨胀':>7}{'有名角色段':>11}{'对话占比':>9}")
tot = Counter()
for ci in TEST_CH:
    c = next(x for x in b['chapters'] if x['index'] == ci)
    turns = speaker.split_turns(c['text'])
    voices = speaker.assign_voices(turns, roster)
    groups = 1 + sum(1 for i in range(1, len(voices)) if voices[i] != voices[i - 1])
    segs, _ = speaker.segment_for_voices(c['text'], roster=roster)
    base = engine.split_segments(c['text'])
    named = sum(1 for s in segs if s['voice'] not in ('旁白', '对话'))
    dlg_chars = sum(len(s['text']) for s in segs if s['voice'] != '旁白')
    allc = sum(len(s['text']) for s in segs)
    print(f"    {ci:>4}{len(turns):>7}{groups:>8}{len(segs):>7}{len(base):>9}"
          f"{len(segs)/len(base):>7.2f}{named:>11}{dlg_chars/allc:>9.1%}")
    tot['turns'] += len(turns); tot['groups'] += groups
    tot['segs'] += len(segs); tot['base'] += len(base)
print(f"    {'合计':>4}{tot['turns']:>7}{tot['groups']:>8}{tot['segs']:>7}{tot['base']:>9}"
      f"{tot['segs']/tot['base']:>7.2f}")
print()

# 详细看一章
c = next(x for x in b['chapters'] if x['index'] == 20)
segs, _ = speaker.segment_for_voices(c['text'], roster=roster)
st = speaker.stats(segs, roster)
print(f"[3] 第 20 章「{c['title']}」剧本统计")
print(f'    段数 {st["n"]}   有名角色的段 {st["named_segs"]} ({st["named_ratio"]:.1%})')
print(f'    各音色段数: {dict(st["by_voice"].most_common())}')
print(f'    各音色字数: {dict(st["chars"].most_common())}')
print()
print('    —— 剧本样例（前 30 段）——')
for s in segs[:30]:
    v = s['voice']
    mark = '○' if v == '旁白' else ('●' if v == '对话' else '◆')
    print(f"      {mark} [{v:<6}] {s['text'][:52]}")

print()
print('[4] 说话人交替样例（确认归属是否合理）')
# 找一段连续对话
for i in range(len(segs) - 8):
    win = segs[i:i + 8]
    vs = [s['voice'] for s in win]
    if len(set(vs)) >= 3 and '旁白' in vs:
        for s in win:
            v = s['voice']
            mark = '○' if v == '旁白' else ('●' if v == '对话' else '◆')
            print(f"      {mark} [{v:<6}] {s['text'][:56]}")
        break
