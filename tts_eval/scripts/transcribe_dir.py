# -*- coding: utf-8 -*-
"""
对任意目录的段 wav 算 CER（跑在 envs\\cosyvoice，那里有 whisper + opencc）。

参考文本通过 --texts 传入 JSON（{seg序号: 原文}），避免跨环境 import 项目模块。

用法:
  python transcribe_dir.py --dir <目录> --texts <ref_texts.json> [--out-tag NAME]
"""
import argparse
import glob
import json
import os
import re
import time

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
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True)
    ap.add_argument('--texts', required=True)
    ap.add_argument('--out-tag', default=None)
    ap.add_argument('--model', default='small')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    with open(args.texts, encoding='utf-8') as f:
        raw = json.load(f)
    ref = {int(k): norm(v) for k, v in raw.items()}

    files = sorted(glob.glob(os.path.join(args.dir, '*.wav')),
                   key=lambda p: int(re.search(r'_seg(\d+)\.wav$', p).group(1)))
    if args.limit:
        files = files[:args.limit]
    print(f'待转写 {len(files)} 段', flush=True)

    m = whisper.load_model(args.model)
    out, t0 = {}, time.time()
    for k, p in enumerate(files, 1):
        i = int(re.search(r'_seg(\d+)\.wav$', p).group(1))
        try:
            r = m.transcribe(p, language='zh', fp16=False)
            h = norm(r.get('text', ''))
        except Exception as e:
            print(f'  seg{i} 失败 {e}', flush=True)
            h = ''
        a = ref.get(i, '')
        out[str(i)] = dict(ref=a, hyp=h,
                           cer=(ed(a, h) / max(len(a), 1)) if a else None,
                           ratio=(len(h) / max(len(a), 1)) if a else None)
        if k % 25 == 0:
            cs = [v['cer'] for v in out.values() if v['cer'] is not None]
            print(f'  {k}/{len(files)}  当前 CER 均值 {sum(cs)/len(cs):.2%}', flush=True)

    tag = args.out_tag or os.path.basename(os.path.normpath(args.dir))
    os.makedirs(os.path.join(EVAL, 'reports'), exist_ok=True)
    dst = os.path.join(EVAL, 'reports', f'cer_{tag}.json')
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    cs = [v['cer'] for v in out.values() if v['cer'] is not None]
    rs = [v['ratio'] for v in out.values() if v['ratio'] is not None]
    drop = sum(1 for r in rs if r < 0.9)
    print(f'\n===== CER 结果（n={len(cs)}）=====')
    print(f'  CER     : 均值 {sum(cs)/len(cs):.2%}  最大 {max(cs):.2%}  '
          f'中位 {sorted(cs)[len(cs)//2]:.2%}')
    print(f'  长度比   : 均值 {sum(rs)/len(rs):.3f}  最低 {min(rs):.3f}  '
          f'漏字段(<0.9) {drop}/{len(rs)}')
    print(f'  用时 {(time.time()-t0)/60:.1f} 分钟')
    print(f'-> {dst}', flush=True)


if __name__ == '__main__':
    main()
