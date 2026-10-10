# -*- coding: utf-8 -*-
"""
下载评测所需模型到 tts_eval\models\。
huggingface.co 在本机不可达，统一走 ModelScope。
"""
import os
import sys
import time

from modelscope import snapshot_download

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models')
JOBS = [
    ('Qwen/Qwen3-TTS-12Hz-1.7B-Base', 'Qwen3-TTS-12Hz-1.7B-Base'),
    ('meituan-longcat/LongCat-AudioDiT-1B', 'LongCat-AudioDiT-1B'),
    ('google/umt5-base', 'umt5-base'),          # LongCat 的文本编码器，需单独下
]

os.makedirs(ROOT, exist_ok=True)
for mid, sub in JOBS:
    dst = os.path.join(ROOT, sub)
    if os.path.isdir(dst) and os.listdir(dst):
        print(f'[skip] {sub} 已存在', flush=True)
        continue
    print(f'[down] {mid} -> {dst}', flush=True)
    t0 = time.time()
    try:
        snapshot_download(mid, local_dir=dst)
        print(f'[done] {sub}  用时 {(time.time()-t0)/60:.1f} 分钟', flush=True)
    except Exception as e:
        print(f'[FAIL] {sub}: {e}', flush=True)

print('\n=== 下载结果 ===', flush=True)
for mid, sub in JOBS:
    dst = os.path.join(ROOT, sub)
    tot = 0
    for r, _, fs in os.walk(dst):
        for f in fs:
            try:
                tot += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    print(f'  {sub:<32} {tot/1e9:6.2f} GB  {"OK" if tot > 1e8 else "!! 不完整"}', flush=True)
