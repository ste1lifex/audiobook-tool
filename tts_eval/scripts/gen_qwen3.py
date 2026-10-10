# -*- coding: utf-8 -*-
"""
Qwen3-TTS 生成器（评测用，跑在 envs\\qwen3tts）。

刻意不做质检门重试、不做停顿校正 —— 目的是测引擎本身的原始表现，
质检结果留到 score.py 统一算（这样能看出每路会被门拦下多少）。

用法:
  python gen_qwen3.py --model-dir <模型目录> --out <输出目录> [--tag 0.6b]
"""
import argparse
import json
import os
import time

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

EVAL = r'tts_eval'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model-dir', required=True)
    ap.add_argument('--out', required=True, help='输出子目录名（在 tts_eval\\audio 下）')
    ap.add_argument('--ref', default=r'reference_audio\ref_tight.wav')
    ap.add_argument('--ref-text',
                    default='')
    ap.add_argument('--segments', default=os.path.join(EVAL, 'scripts', 'segments.json'))
    ap.add_argument('--batch', type=int, default=4)
    ap.add_argument('--temperature', type=float, default=0.5)
    ap.add_argument('--top-p', type=float, default=0.9)
    ap.add_argument('--rep-pen', type=float, default=1.15)
    ap.add_argument('--max-new-tokens', type=int, default=2048)
    args = ap.parse_args()

    out_dir = os.path.join(EVAL, 'audio', args.out)
    os.makedirs(out_dir, exist_ok=True)
    with open(args.segments, encoding='utf-8') as f:
        segs = json.load(f)

    print(f'模型: {args.model_dir}', flush=True)
    print(f'参考: {args.ref}', flush=True)
    print(f'段落: {len(segs)} 条, batch={args.batch}', flush=True)
    print(f'采样: temp={args.temperature} top_p={args.top_p} rep={args.rep_pen} '
          f'max_new={args.max_new_tokens}', flush=True)
    print('加载模型...', flush=True)
    t0 = time.time()
    model = Qwen3TTSModel.from_pretrained(
        args.model_dir, device_map='cuda:0', dtype=torch.bfloat16)
    prompt = model.create_voice_clone_prompt(
        ref_audio=args.ref, ref_text=args.ref_text, x_vector_only_mode=False)
    print(f'模型就绪 ({time.time()-t0:.0f}s)', flush=True)

    kw = dict(temperature=args.temperature, top_p=args.top_p,
              repetition_penalty=args.rep_pen, max_new_tokens=args.max_new_tokens)
    rows = []
    t_start = time.time()
    for b in range(0, len(segs), args.batch):
        batch = segs[b:b + args.batch]
        texts = [r['text'] for r in batch]
        tb = time.time()
        try:
            wavs, sr = model.generate_voice_clone(
                text=texts, language=['Chinese'] * len(texts),
                voice_clone_prompt=prompt, **kw)
        except Exception as e:
            print(f'  batch {b} 失败: {e} → 逐段重试', flush=True)
            torch.cuda.empty_cache()
            wavs, sr = [], 24000
            for t in texts:
                try:
                    w, s = model.generate_voice_clone(
                        text=t, language='Chinese', voice_clone_prompt=prompt, **kw)
                    wavs.append(w[0])
                    sr = s
                except Exception as e2:
                    print(f'    单段失败: {e2}', flush=True)
                    wavs.append(None)
        dt = time.time() - tb
        for off, w in enumerate(wavs):
            r = batch[off]
            if w is None:
                rows.append(dict(idx=r['idx'], chars=r['chars'], ok=False, err='gen_failed'))
                continue
            if w.dtype != np.float32:
                w = w.astype(np.float32)
            dur = len(w) / sr
            p = os.path.join(out_dir, f'seg{r["idx"]:02d}.wav')
            sf.write(p, w, sr)
            rows.append(dict(idx=r['idx'], chars=r['chars'], ok=True, sr=int(sr),
                             dur=round(dur, 3), wav=os.path.basename(p)))
            print(f'  seg{r["idx"]:02d} {r["chars"]:3d}字 -> {dur:6.2f}s  '
                  f'(本批 {dt:.0f}s)', flush=True)
        print(f'  [{min(b+args.batch,len(segs))}/{len(segs)}] 累计 {(time.time()-t_start)/60:.1f} min',
              flush=True)

    meta = dict(engine='qwen3', model_dir=args.model_dir, ref=args.ref,
                ref_text=args.ref_text, batch=args.batch,
                temperature=args.temperature, top_p=args.top_p,
                repetition_penalty=args.rep_pen,
                max_new_tokens=args.max_new_tokens,
                total_sec=round(time.time() - t_start, 1), rows=rows)
    with open(os.path.join(out_dir, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    ok = [r for r in rows if r.get('ok')]
    tot_audio = sum(r['dur'] for r in ok)
    print(f'\n完成: {len(ok)}/{len(rows)} 段, 音频合计 {tot_audio:.1f}s, '
          f'耗时 {meta["total_sec"]/60:.1f} min, RTF {meta["total_sec"]/max(tot_audio,1e-9):.2f}',
          flush=True)
    print(f'-> {out_dir}', flush=True)


if __name__ == '__main__':
    main()
