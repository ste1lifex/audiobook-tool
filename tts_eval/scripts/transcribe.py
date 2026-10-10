# -*- coding: utf-8 -*-
"""
用 whisper 转写各引擎产出的音频，转成简体并去标点，供 score.py 算 CER。

跑在 envs\\cosyvoice（那里有 whisper，small.pt 已缓存，也有 opencc）。

CER 用来抓长文本最容易翻车的问题：漏字、重复、错读、乱读——这些是"音色像不像"
指标看不出来的。刻意不使用 initial_prompt 引导，避免人为压低 CER。

用法: python transcribe.py [--engines qwen3-0.6b qwen3-1.7b longcat-1b]
"""
import argparse
import glob
import json
import os
import re
import time

import whisper

EVAL = r'tts_eval'
REPORTS = os.path.join(EVAL, 'reports')
os.makedirs(REPORTS, exist_ok=True)

PUNCT = re.compile(r'[\s，。、！？；：「」『』“”‘’（）()\[\]{}…—－\-.,!?;:\'"]+')


def normalize(s, simp=True):
    s = PUNCT.sub('', s)
    if simp:
        try:
            from opencc import OpenCC
            s = OpenCC('t2s').convert(s)
        except Exception:
            pass
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engines', nargs='*',
                    default=['qwen3-0.6b', 'qwen3-1.7b', 'longcat-1b'])
    ap.add_argument('--model', default='small')
    args = ap.parse_args()

    with open(os.path.join(EVAL, 'scripts', 'segments.json'), encoding='utf-8') as f:
        segs = json.load(f)
    src = {r['idx']: normalize(r['text']) for r in segs}

    print(f'加载 whisper {args.model} ...', flush=True)
    model = whisper.load_model(args.model)
    print('就绪', flush=True)

    for eng in args.engines:
        d = os.path.join(EVAL, 'audio', eng)
        wavs = sorted(glob.glob(os.path.join(d, 'seg*.wav')))
        if not wavs:
            print(f'[skip] {eng}: 无音频', flush=True)
            continue
        out = {}
        t0 = time.time()
        for p in wavs:
            idx = int(re.search(r'seg(\d+)\.wav$', p).group(1))
            try:
                r = model.transcribe(p, language='zh', fp16=False)
                raw = r.get('text', '')
            except Exception as e:
                print(f'  {eng} seg{idx} 转写失败: {e}', flush=True)
                raw = ''
            out[str(idx)] = dict(raw=raw, norm=normalize(raw),
                                 ref=src.get(idx, ''))
        with open(os.path.join(REPORTS, f'transcript_{eng}.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f'[{eng}] {len(out)} 段转写完成，用时 {time.time()-t0:.0f}s '
              f'-> reports/transcript_{eng}.json', flush=True)
        # 立刻给个 CER 粗值
        err = tot = 0
        for v in out.values():
            a, b = v['ref'], v['norm']
            tot += max(len(a), 1)
            err += _edit(a, b)
        print(f'    CER(去标点/简体) ≈ {err/max(tot,1):.2%}', flush=True)


def _edit(a, b):
    """Levenshtein 距离。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


if __name__ == '__main__':
    main()
