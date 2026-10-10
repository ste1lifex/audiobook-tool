# -*- coding: utf-8 -*-
"""
用 whisper 转写 A(旧切分) 和 B(新切分)，对比同一段原文的覆盖情况。
用于确证：旧版那段是不是真的丢了内容（而不只是"听起来差"）。

需在 envs\cosyvoice 下运行（有 whisper + opencc）。
"""
import json
import os
import re
import sys

import whisper
import opencc

AB = r'tts_eval\audio\_ab_ch33'

# 原文：v1 seg56+57+58（与 A/B 音频对应的同一段文字）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

BOOK_DIR = os.path.join(config.OUTPUT_BASE, '海伯利安的隕落')
with open(os.path.join(BOOK_DIR, '_chapters.json'), encoding='utf-8') as f:
    chapters = json.load(f)
text = next(c for c in chapters if c['index'] == 33)['text']


def split_v1(text, max_chars=105):
    text = re.sub(r'\s+', '', text)
    parts = re.split(r'(?<=[。！？；…])', text)
    segs, buf = [], ''
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) <= max_chars:
            buf += p
        else:
            if buf:
                segs.append(buf)
                buf = p
            else:
                sub = re.split(r'(?<=[，、：])', p)
                b2 = ''
                for s2 in sub:
                    if len(b2) + len(s2) <= max_chars:
                        b2 += s2
                    else:
                        if b2:
                            segs.append(b2)
                        b2 = s2
                if b2:
                    buf = b2
    if buf:
        segs.append(buf)
    return [s for s in segs if s.strip()]


v1 = split_v1(text)
src = ''.join(v1[56:59])                      # A/B 音频对应的原文
cc = opencc.OpenCC('t2s')


def norm(s):
    s = cc.convert(s)
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s)


src_n = norm(src)
print(f'原文: {len(src)} 字（去标点 {len(src_n)}）')
print()

model = whisper.load_model('small', device='cuda')
rows = []
for tag, fn in (('A 旧切分(v1)', 'A_旧切分_v1.wav'), ('B 新切分(v2)', 'B_新切分_v2.wav')):
    p = os.path.join(AB, fn)
    r = model.transcribe(p, language='zh', verbose=False)
    got = norm(r['text'])
    # 用"最长公共子序列比例"衡量覆盖（顺序敏感，能反映漏段而非仅字数）
    import difflib
    sm = difflib.SequenceMatcher(None, src_n, got)
    cover = sum(b.size for b in sm.get_matching_blocks()) / max(1, len(src_n))
    rows.append((tag, len(got), cover))
    print(f'{tag}')
    print(f'   转写出 {len(got)} 字   长度比 {len(got)/len(src_n):.3f}   '
          f'覆盖原文比例 {cover:.3f}')
    print(f'   转写开头: {r["text"][:90]}')
    print(f'   转写结尾: ...{r["text"][-90:]}')
    print()

print('=== 结论 ===')
a, b = rows[0], rows[1]
print(f'  A 覆盖原文 {a[2]:.1%}     B 覆盖原文 {b[2]:.1%}')
if a[2] < b[2] - 0.05:
    print(f'  >>> 旧切分确实丢内容：少覆盖 {(b[2]-a[2])*100:.1f} 个百分点')
else:
    print('  >>> 两者覆盖接近，旧版没丢内容（那问题只在音质）')
