# -*- coding: utf-8 -*-
"""
CER 噪声底标定 + 错误模式诊断（跑在 envs\\cosyvoice）。

问题: 三路 CER 都在 ~10% 且几乎一样，怀疑是 whisper 自身转写误差主导。
做法: 用同一段"已知文本的真实人声"（ref_clip_full.wav，参考音色原声，
      文本已知：""）
      过同一个 whisper → 这就是人类真人语音在该指标下的 CER 下限。
另外把各引擎的实际错误打出来，看是系统性专名错还是随机错。
"""
import json
import os
import re
import sys

import whisper

EVAL = r'tts_eval'
PUNCT = re.compile(r'[\s，。、！？；：「」『』“”‘’（）()\[\]{}…—－\-.,!?;:\'"]+')


def norm(s):
    s = PUNCT.sub('', s)
    try:
        from opencc import OpenCC
        s = OpenCC('t2s').convert(s)
    except Exception:
        pass
    return s


def ed(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def align(a, b):
    """打印插入/删除/替换的直观对齐。"""
    out, i, j = [], 0, 0
    # 简化的逐位比较（长度接近时够用）
    for k in range(max(len(a), len(b))):
        ca = a[k] if k < len(a) else '·'
        cb = b[k] if k < len(b) else '·'
        out.append(ca if ca == cb else f'[{ca}→{cb}]')
    return ''.join(out)


m = whisper.load_model('small')

print('=== 噪声底：真实人声 + 已知文本 ===')
REF_TXT = ''
for f, tag in ((r'reference_audio\ref_clip_full.wav', '原始8.6s'),
               (r'reference_audio\ref_tight.wav', '紧凑6.08s')):
    r = m.transcribe(f, language='zh', fp16=False)
    hyp = norm(r['text'])
    ref = norm(REF_TXT)
    c = ed(ref, hyp) / len(ref)
    print(f'  [{tag}] 真人语音 CER = {c:.2%}   (n={len(ref)}字)')
    print(f'    参考: {ref}')
    print(f'    转写: {hyp}')

print('\n=== 各引擎实际错误（前 4 段）===')
for eng in ('qwen3-0.6b', 'qwen3-1.7b', 'longcat-1b'):
    p = os.path.join(EVAL, 'reports', f'transcript_{eng}.json')
    if not os.path.exists(p):
        continue
    d = json.load(open(p, encoding='utf-8'))
    print(f'\n--- {eng} ---')
    for k in sorted(d, key=lambda x: int(x))[:4]:
        v = d[k]
        a, b = v['ref'], v['norm']
        c = ed(a, b) / max(len(a), 1)
        print(f'  seg{int(k):02d} CER={c:5.1%}')
        print(f'    参考: {a}')
        print(f'    转写: {b}')
