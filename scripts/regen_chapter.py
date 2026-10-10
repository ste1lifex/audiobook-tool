# -*- coding: utf-8 -*-
"""
重做指定章节（带段级断点续跑 + 质检门），产物与旧版并存便于对比。

用法:
  python regen_chapter.py --book 海伯利安 --ch 6              # 重做第六章 → _v2
  python regen_chapter.py --book 海伯利安 --ch 6 --suffix v3
  python regen_chapter.py --book 海伯利安 --ch 6 --limit 12    # 只跑前12段(验证)

产物:
  <book>/_chunks_<suffix>/        段级 wav（续跑时已存在则跳过）
  <book>/_regen_<suffix>_ch06.json 每段的质检指标与耗时（质检用）
  <book>/<book>_ch06_<章名>_<suffix>.mp3
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import engine  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', default='海伯利安')
    ap.add_argument('--ch', type=int, default=6, help='章序号(1-based)')
    ap.add_argument('--suffix', default='v2')
    ap.add_argument('--limit', type=int, default=0, help='只跑前 N 段（验证用）')
    ap.add_argument('--ref', default=config.DEFAULT_REF_AUDIO)
    ap.add_argument('--ref-text', default=config.DEFAULT_REF_TEXT)
    args = ap.parse_args()

    book_dir = os.path.join(config.OUTPUT_BASE, args.book)
    if not os.path.isdir(book_dir):
        raise SystemExit(f'找不到书目目录: {book_dir}')
    ch_path = os.path.join(book_dir, '_chapters.json')
    with open(ch_path, encoding='utf-8') as f:
        chapters = json.load(f)
    ch = next((c for c in chapters if c['index'] == args.ch), None)
    if ch is None:
        raise SystemExit(f'找不到第 {args.ch} 章')

    segs = engine.split_segments(ch['text'])
    if args.limit:
        segs = segs[:args.limit]
    chunk_dir = os.path.join(book_dir, f'_chunks_{args.suffix}')
    os.makedirs(chunk_dir, exist_ok=True)
    prefix = f"{engine.safe_name(args.book)}_ch{ch['index']:02d}"

    print(f'== 重做《{args.book}》第 {ch["index"]} 章「{ch["title"]}」 ==', flush=True)
    print(f'   段数 {len(segs)}  字数 {len(ch["text"])}', flush=True)
    print(f'   参考音色 {os.path.basename(args.ref)}', flush=True)
    print(f'   采样参数 {engine._gen_kwargs()}', flush=True)
    print(f'   段目录 {chunk_dir}', flush=True)

    model, prompt = engine.get_model()
    engine.set_reference(args.ref, args.ref_text)
    gate = engine.get_gate(args.ref)

    # 断点：已有段直接复用
    done = {}
    for p in glob.glob(os.path.join(chunk_dir, f'{prefix}_seg*.wav')):
        try:
            i = int(os.path.basename(p).split('_seg')[1].split('.')[0])
            w, _ = sf.read(p)
            done[i] = w
        except Exception:
            pass
    pending = [(i, s) for i, s in enumerate(segs) if i not in done]
    print(f'   已有 {len(done)} 段，待生成 {len(pending)} 段', flush=True)

    report_path = os.path.join(book_dir, f'_regen_{args.suffix}_ch{ch["index"]:02d}.json')
    report = []
    if os.path.exists(report_path):
        try:
            with open(report_path, encoding='utf-8') as f:
                report = json.load(f)
        except Exception:
            report = []
    stats = {'checked': 0, 'regen': 0, 'ok_first': 0, 'kept_best': 0}

    t_start = time.time()
    bs = config.BATCH_SIZE
    for b in range(0, len(pending), bs):
        batch = pending[b:b + bs]
        idxs = [x[0] for x in batch]
        texts = [x[1] for x in batch]
        t0 = time.time()
        notes = {}
        try:
            wavs, sr = model.generate_voice_clone(
                text=texts, language=['Chinese'] * len(texts),
                voice_clone_prompt=prompt,
                max_new_tokens=config.MAX_NEW_TOKENS, **engine._gen_kwargs())
        except Exception as e:
            print(f'   batch {idxs} 失败: {e} → 逐段重试', flush=True)
            engine._clear_cache()
            wavs, sr = [], config.SAMPLE_RATE
            for t in texts:
                try:
                    w, s = engine._gen_one(model, prompt, t)
                    wavs.append(w)
                    sr = s
                except Exception as e2:
                    print(f'     单段失败: {e2}', flush=True)
                    wavs.append(None)

        for off, w in enumerate(wavs):
            i = idxs[off]
            if w is None:
                continue
            if w.dtype != np.float32:
                w = w.astype(np.float32)
            ok, why, m = engine._gate_check(w, sr, gate)
            note = 'ok'
            if not ok:
                stats['regen'] += 1
                print(f'   seg{i} 质检不合格 [{why}] → 重生成', flush=True)
                wg, srg, note = engine._gen_guarded_v2(model, prompt, texts[off], gate)
                if wg is not None:
                    w, sr = wg, srg
                    _, _, m2 = engine._gate_check(w, sr, gate)
                    m = m2
                if note == 'ok' or note.startswith('ok('):
                    pass
                elif '保留最好' in note:
                    stats['kept_best'] += 1
            else:
                stats['ok_first'] += 1
            stats['checked'] += 1
            sf.write(os.path.join(chunk_dir, f'{prefix}_seg{i:05d}.wav'), w, sr)
            done[i] = w
            report.append(dict(seg=i, dur=round(len(w) / sr, 1),
                               sim=round(m.get('sim', 0), 4),
                               win_sim=round(m.get('win_sim', 0), 4),
                               flat=round(m.get('flatness', 0), 4),
                               note=note, why=why))
        el = time.time() - t_start
        donen = stats['checked']
        eta = (el / donen) * (len(pending) - donen) if donen else 0
        print(f'   [{b+len(batch)}/{len(pending)}] 本批 {time.time()-t0:.0f}s  '
              f'已用 {el/60:.1f}min  预计剩余 {eta/60:.1f}min  '
              f'(一次通过 {stats["ok_first"]} / 重生成 {stats["regen"]})', flush=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=1)

    # 拼接本章
    if len(done) < len(segs):
        missing = [i for i in range(len(segs)) if i not in done]
        if args.limit:
            print(f'\n--limit 模式: 只拼接已生成的 {len(done)} 段'
                  f'（后面的 {len(missing)} 段本次未跑）', flush=True)
        else:
            print(f'!! 仍有 {len(missing)} 段缺失，暂不拼接: {missing[:20]}', flush=True)
            return
    order = sorted(done.keys())
    sil = np.zeros(int(config.SAMPLE_RATE * config.SEG_SILENCE_SEC), dtype=np.float32)
    parts = []
    for k, i in enumerate(order):
        w = done[i]
        if w.dtype != np.float32:
            w = w.astype(np.float32)
        parts.append(w)
        if k < len(order) - 1:
            parts.append(sil)
    full = np.concatenate(parts)
    wav_path = os.path.join(book_dir, f'{prefix}_全章_{args.suffix}.wav')
    mp3_path = os.path.join(book_dir, f'{prefix}_{engine.safe_name(ch["title"])}_{args.suffix}.mp3')
    sf.write(wav_path, full, config.SAMPLE_RATE)
    subprocess.run([config.FFMPEG, '-y', '-i', wav_path, '-codec:a', 'libmp3lame',
                    '-qscale:a', '2', mp3_path], check=False, capture_output=True)
    os.remove(wav_path)

    sims = [r['sim'] for r in report if r['sim'] > 0]
    print(f'\n== 完成 ==', flush=True)
    print(f'   {mp3_path}', flush=True)
    print(f'   时长 {len(full)/config.SAMPLE_RATE/60:.1f} 分钟  '
          f'大小 {os.path.getsize(mp3_path)/1024/1024:.1f} MB', flush=True)
    print(f'   总耗时 {(time.time()-t_start)/60:.1f} 分钟', flush=True)
    print(f'   质检: 一次通过 {stats["ok_first"]} / 重生成 {stats["regen"]} / '
          f'保留最好 {stats["kept_best"]} / 共 {stats["checked"]}', flush=True)
    if sims:
        print(f'   声纹相似度: 均值 {np.mean(sims):.3f}  最低 {np.min(sims):.3f}  '
              f'低于0.62的 {sum(1 for s in sims if s < 0.62)} 段', flush=True)
    print(f'   质检报告 {report_path}', flush=True)


if __name__ == '__main__':
    main()
