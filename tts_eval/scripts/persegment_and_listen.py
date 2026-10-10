# -*- coding: utf-8 -*-
"""
逐段对照表 + 试听文件生成（跑在 envs\\qwen3tts）。

产出:
  reports/persegment.md                 逐段四指标对照，看是否"段段都赢"
  audio/_listen/<engine>_all12.wav      每引擎 12 段连续拼接（便于整体横向试听）
  audio/_listen/cmp_segNN.wav           同一句的三种引擎版本，0.6s 静音分隔
"""
import json
import os
import re
import sys

import numpy as np
import soundfile as sf

EVAL = r'tts_eval'
ENGINES = ['qwen3-0.6b', 'qwen3-1.7b', 'longcat-1b']
LABEL = {'qwen3-0.6b': 'Qwen3-0.6B', 'qwen3-1.7b': 'Qwen3-1.7B',
         'longcat-1b': 'LongCat-1B'}


def load(eng, idx):
    p = os.path.join(EVAL, 'audio', eng, f'seg{idx:02d}.wav')
    if not os.path.exists(p):
        return None, None
    w, sr = sf.read(p)
    if w.ndim > 1:
        w = w.mean(axis=1)
    return w.astype(np.float32), sr


# ---- 逐段对照表 ----
scores = {}
for e in ENGINES:
    p = os.path.join(EVAL, 'reports', f'score_{e}.json')
    if os.path.exists(p):
        d = json.load(open(p, encoding='utf-8'))
        scores[e] = {r['idx']: r for r in d['rows']}

idx_all = sorted({i for e in scores for i in scores[e]})
lines = ['# 逐段对照（同句不同引擎）', '',
         '每格: 相似度 / CER。**粗体**=该行最好。', '']
hdr = '| seg | 字数 | ' + ' | '.join(LABEL[e] for e in ENGINES if e in scores) + ' |'
lines += [hdr, '|---|---|' + '---|' * len([e for e in ENGINES if e in scores])]

win = {e: 0 for e in ENGINES}
for i in idx_all:
    cells, best_sim, best_cer = [], -9, 9
    for e in ENGINES:
        if e not in scores or i not in scores[e]:
            continue
        r = scores[e][i]
        s = r.get('sim')
        c = r.get('cer')
        if s is not None and s > best_sim:
            best_sim = s
        if c is not None and c < best_cer:
            best_cer = c
    for e in ENGINES:
        if e not in scores or i not in scores[e]:
            continue
        r = scores[e][i]
        s, c = r.get('sim'), r.get('cer')
        st = '—' if s is None else f'{s:.3f}'
        ct = '—' if c is None else f'{c*100:.0f}%'
        if s is not None and abs(s - best_sim) < 1e-9:
            st = f'**{st}**'
        if c is not None and abs(c - best_cer) < 1e-9:
            ct = f'**{ct}**'
        cells.append(f'{st} / {ct}')
        if s is not None and abs(s - best_sim) < 1e-9:
            win[e] += 1
    ch = next((scores[e][i]['chars'] for e in ENGINES if e in scores and i in scores[e]), '')
    lines.append(f'| {i:02d} | {ch} | ' + ' | '.join(cells) + ' |')

lines += ['', '## 段级胜场（相似度最高）', '']
for e in ENGINES:
    if e in scores:
        lines.append(f'- {LABEL[e]}: **{win[e]}/{len(idx_all)}** 段')
with open(os.path.join(EVAL, 'reports', 'persegment.md'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines) + '\n')
print('\n'.join(lines))

# ---- 试听文件 ----
LD = os.path.join(EVAL, 'audio', '_listen')
os.makedirs(LD, exist_ok=True)
GAP = 0.45
for e in ENGINES:
    sr0 = None
    parts = []
    for k, i in enumerate(idx_all):
        w, sr = load(e, i)
        if w is None:
            continue
        sr0 = sr
        parts.append(w)
        parts.append(np.zeros(int(sr * GAP), dtype=np.float32))
    if parts:
        full = np.concatenate(parts)
        p = os.path.join(LD, f'{e}_all12.wav')
        sf.write(p, full, sr0)
        print(f'  {p}  ({len(full)/sr0:.1f}s)')

for i in idx_all:
    parts, sr0 = [], 24000
    for e in ENGINES:
        w, sr = load(e, i)
        if w is None:
            continue
        sr0 = sr
        parts.append(w)
        parts.append(np.zeros(int(sr * 0.6), dtype=np.float32))
    if parts:
        full = np.concatenate(parts)
        sf.write(os.path.join(LD, f'cmp_seg{i:02d}.wav'), full, sr0)
print(f'\n-> {LD}  (cmp_segNN.wav 顺序: '
      f'{" -> ".join(LABEL[e] for e in ENGINES)})')
