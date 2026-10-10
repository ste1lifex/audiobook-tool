# -*- coding: utf-8 -*-
"""
段间一致性审计 v2 —— 已修正"段时长"混淆。

v1 的缺陷：sim_centroid 与段时长相关 r=+0.566（短段有声帧少、声纹向量抖），
          会把"段短"误判成"漂移"。
v2 的修正：先用时长分桶算出"该时长下的期望一致性"，再取残差；
          用残差的 中位-3MAD 作为离群判据。这样短段不再被冤枉。

用法:
  # 快速看趋势（前几章）
  python consistency_audit2.py --ch 1 2 3 --beep 6
  # 全书 46 章（后台跑，约 25 分钟）
  python consistency_audit2.py --all --save
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf

EVAL = r'tts_eval'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quality import SpeakerScorer  # noqa: E402

REF = r'reference_audio\ref_tight.wav'
FFMPEG = r'bin\ffmpeg.exe'
SRC_M4A = r'reference_audio\ref_source_audio.m4a'
ANCHOR_WAV = os.path.join(EVAL, 'tmp', '_anchor_400s.wav')
BOOKS = {
    '海伯利安的隕落': (r'output\海伯利安的隕落', 46),
    '安迪密恩': (r'output\安迪密恩', 60),
    '安迪密恩的覺醒': (r'output\安迪密恩的覺醒', 35),
}


def embed_vad(sc, wav, sr, rel=0.12, abs_floor=0.004):
    """只统计有声帧的声纹向量。"""
    import torch
    import torchaudio
    w = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)).unsqueeze(0)
    if sr != 16000:
        w = torchaudio.functional.resample(w, sr, 16000)
    feat = sc.torchaudio.compliance.kaldi.fbank(
        w, num_mel_bins=80, dither=0.0, sample_frequency=16000)
    y = w.squeeze(0).numpy()
    fl, fs = 400, 160
    nf = 1 + (len(y) - fl) // fs if len(y) >= fl else 0
    if nf <= 0:
        return None
    fr = np.lib.stride_tricks.as_strided(
        y, shape=(nf, fl), strides=(y.strides[0] * fs, y.strides[0])).astype(np.float64)
    rms = np.sqrt(np.mean(fr ** 2, axis=1))
    th = max(abs_floor, rel * float(np.percentile(rms, 90)))
    sel = rms >= th
    if sel.sum() < 10:
        sel = rms >= np.percentile(rms, 50)
    if sel.sum() < 5:
        return None
    f = feat.numpy()[sel]
    f = f - f.mean(axis=0, keepdims=True)
    out = sc.sess.run(None, {'input': f[np.newaxis].astype(np.float32)})
    e = out[0][0]
    return e / (np.linalg.norm(e) + 1e-9)


def normalize_by_duration(dur, score, bin_w=2.0):
    """按 2s 分桶算该时长下的期望分，返回残差（正=比同长度段更像邻居）。"""
    exp = np.full(len(score), np.nan)
    lo = 0.0
    while lo < dur.max() + bin_w:
        m = (dur >= lo) & (dur < lo + bin_w)
        if m.sum() >= 5:
            exp[m] = np.median(score[m])
        lo += bin_w
    # 桶内样本不足的，用全局中位
    exp[np.isnan(exp)] = np.median(score)
    return score - exp, exp


def run_book(sc, anchor, refe, book, ch_list, verbose=True):
    book_dir, _ = BOOKS[book]
    cdir = os.path.join(book_dir, '_chunks')
    all_rows = []
    for ch in ch_list:
        files = sorted(glob.glob(os.path.join(cdir, f'{book}_ch{ch:02d}_seg*.wav')),
                       key=lambda p: int(re.search(r'_seg(\d+)\.wav$', p).group(1)))
        if not files:
            continue
        embs, durs, segs = [], [], []
        for p in files:
            w, sr = sf.read(p)
            if w.ndim > 1:
                w = w.mean(axis=1)
            e = embed_vad(sc, w, sr)
            embs.append(e)
            durs.append(len(w) / sr)
            segs.append(int(re.search(r'_seg(\d+)\.wav$', p).group(1)))
        idx = [i for i, e in enumerate(embs) if e is not None]
        if len(idx) < 5:
            continue
        E = np.stack([embs[i] for i in idx])
        D = np.array([durs[i] for i in idx])
        S = E.sum(axis=0)
        n = len(idx)
        rows = []
        t = 0.0
        for k, i in enumerate(idx):
            e = E[k]
            loo = (S - e) / max(1, n - 1)
            loo /= (np.linalg.norm(loo) + 1e-9)
            rows.append(dict(ch=ch, seg=segs[i], dur=round(float(D[k]), 2),
                             t_in_ch=round(t, 1),
                             sim_anchor=round(float(np.dot(e, anchor)), 4),
                             sim_ref=round(float(np.dot(e, refe)), 4),
                             sim_centroid=round(float(np.dot(e, loo)), 4),
                             sim_prev=None if k == 0 else round(float(np.dot(e, E[k-1])), 4)))
            t += float(D[k])
        sc_ = np.array([r['sim_centroid'] for r in rows])
        dd = np.array([r['dur'] for r in rows])
        resid, _ = normalize_by_duration(dd, sc_)
        for r, v in zip(rows, resid):
            r['resid'] = round(float(v), 4)
        all_rows.extend(rows)
        if verbose:
            med = np.median(resid)
            mad = np.median(np.abs(resid - med)) or 1e-6
            nf = int((resid < med - 3 * mad).sum())
            print(f'  ch{ch:02d} n={len(rows):>3}  一致性 {sc_.mean():.4f}  '
                  f'残差中位 {med:+.4f} 3MAD {3*mad:.4f}  标记 {nf}', flush=True)
    return all_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', default='海伯利安的隕落')
    ap.add_argument('--ch', type=int, nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--beep', type=int, default=0, help='导出带提示音的前 N 个试听文件')
    ap.add_argument('--save', action='store_true')
    ap.add_argument('--min-dur', type=float, default=5.0, help='短于此的段不参与判定')
    args = ap.parse_args()

    os.makedirs(os.path.dirname(ANCHOR_WAV), exist_ok=True)
    if not os.path.exists(ANCHOR_WAV):
        subprocess.run([FFMPEG, '-y', '-v', 'quiet', '-ss', '400', '-t', '12',
                        '-i', SRC_M4A, '-ar', '16000', '-ac', '1', ANCHOR_WAV], check=True)
    sc = SpeakerScorer()
    aw, asr = sf.read(ANCHOR_WAV)
    if aw.ndim > 1:
        aw = aw.mean(axis=1)
    anchor = embed_vad(sc, aw, asr)
    rw, rsr = sf.read(REF)
    if rw.ndim > 1:
        rw = rw.mean(axis=1)
    refe = embed_vad(sc, rw, rsr)

    _, total_ch = BOOKS[args.book]
    chs = list(range(1, total_ch + 1)) if args.all else (args.ch or [1, 2, 3])
    print(f'=== {args.book}  ch{chs[0]}..ch{chs[-1]}  ({len(chs)} 章) ===', flush=True)
    rows = run_book(sc, anchor, refe, args.book, chs)
    if not rows:
        print('无数据')
        return

    dur = np.array([r['dur'] for r in rows])
    sa = np.array([r['sim_anchor'] for r in rows])
    sct = np.array([r['sim_centroid'] for r in rows])
    res = np.array([r['resid'] for r in rows])
    keep = dur >= args.min_dur

    med = float(np.median(res[keep]))
    mad = float(np.median(np.abs(res[keep] - med))) or 1e-6
    thr = med - 3 * mad
    flag = keep & (res < thr)
    gate_bad = sa < 0.62

    print(f'\n{"="*78}\n合计 n={len(rows)}  （参与判定 {keep.sum()} 段，>{args.min_dur}s）')
    print(f'  sim_anchor   均值 {sa.mean():.4f} sd {sa.std():.4f} 最低 {sa.min():.4f}')
    print(f'  sim_centroid 均值 {sct.mean():.4f} sd {sct.std():.4f} 最低 {sct.min():.4f}')
    print(f'  时长归一后残差 中位 {med:+.4f}  3MAD {3*mad:.4f}  阈值 {thr:+.4f}')
    print(f'\n  现有门(anchor<0.62) 判不合格 : {int(gate_bad.sum())} 段')
    print(f'  时长归一后的一致性离群       : {int(flag.sum())} 段 '
          f'({flag.sum()/max(1,keep.sum()):.2%})  ← 建议重生成的')
    print(f'  时长相关性(修正后)           : r = {np.corrcoef(dur[keep], res[keep])[0,1]:+.3f} '
          f'(修正前 +0.566)')

    top = sorted([r for r in rows if flag[rows.index(r)]], key=lambda x: x['resid'])
    print(f'\n  最该重生成的 15 段:')
    print(f'    {"章":>5}{"段":>6}{"章内秒":>9}{"时长":>7}{"锚点":>8}{"一致性":>9}{"残差":>9}')
    for r in top[:15]:
        print(f"    ch{r['ch']:<3d}{r['seg']:>6}{r['t_in_ch']:>9.1f}{r['dur']:>7.1f}"
              f"{r['sim_anchor']:>8.4f}{r['sim_centroid']:>9.4f}{r['resid']:>+9.4f}")

    if args.save:
        os.makedirs(os.path.join(EVAL, 'reports'), exist_ok=True)
        tag = args.book + ('_all' if args.all else '_' + '_'.join(str(c) for c in chs))
        dst = os.path.join(EVAL, 'reports', f'consistency2_{tag}.json')
        with open(dst, 'w', encoding='utf-8') as f:
            json.dump(dict(book=args.book, chapters=chs, rows=rows,
                           stats=dict(n=len(rows), kept=int(keep.sum()),
                                      resid_med=med, resid_3mad=3*mad,
                                      flagged=int(flag.sum()),
                                      gate_bad=int(gate_bad.sum()),
                                      min_dur=args.min_dur)), f,
                      ensure_ascii=False, indent=1)
        print(f'\n-> {dst}')

    if args.beep and top:
        out = r'tts_eval\audio\_drift_check2'
        os.makedirs(out, exist_ok=True)
        cdir = os.path.join(BOOKS[args.book][0], '_chunks')
        for n, r in enumerate(top[:args.beep], 1):
            parts, sr = [], 24000
            for off in (-1, 0, 1):
                p = os.path.join(cdir, f"{args.book}_ch{r['ch']:02d}_seg{r['seg']+off:05d}.wav")
                if os.path.exists(p):
                    w, sr = sf.read(p)
                    if w.ndim > 1:
                        w = w.mean(axis=1)
                    w = w.astype(np.float32)
                    if off == 0:
                        beep = (0.25 * np.sin(2 * np.pi * 1500 *
                                np.arange(int(sr * 0.15)) / sr)).astype(np.float32)
                        parts.append(beep)
                    parts.append(w)
            if not parts:
                continue
            sil = np.zeros(int(sr * 0.4), dtype=np.float32)
            seq = []
            for i, w in enumerate(parts):
                if i:
                    seq.append(sil)
                seq.append(w)
            a = np.concatenate(seq)
            a = (a / (float(np.max(np.abs(a))) or 1.0) * 0.95).astype(np.float32)
            nm = f"{n:02d}_ch{r['ch']}_seg{r['seg']:05d}_残差{r['resid']:+.3f}_时长{r['dur']:.0f}s.wav"
            sf.write(os.path.join(out, nm), a, sr)
            print(f'  试听: {nm}  （提示音后就是被判离群的那一段）')
        print(f'  -> {out}')


if __name__ == '__main__':
    main()
