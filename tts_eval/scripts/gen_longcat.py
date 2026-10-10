# -*- coding: utf-8 -*-
"""
LongCat-AudioDiT 生成器（评测用，跑在 envs\\longcat）。

要点:
  * 模型 config 内嵌 text_encoder_config，构造时不需要访问 HF；
    只有 tokenizer 需要 umt5-base，指向本地目录即可（huggingface.co 在本机不可达）。
  * 时长按官方 inference.py 的逻辑估算：中文 0.21 秒/字，并被 max_wav_duration(30s) 截断；
    带参考音频时预算要扣掉参考占用的时长。
  * 同样不做质检/停顿后处理。

用法:
  python gen_longcat.py --out longcat-1b
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import soundfile as sf
import torch

EVAL = r'tts_eval'
REPO = os.path.join(EVAL, 'repos', 'LongCat-AudioDiT')
sys.path.insert(0, REPO)

from transformers import AutoTokenizer  # noqa: E402
from utils import approx_duration_from_text, load_audio, normalize_text  # noqa: E402

import audiodit  # noqa: E402,F401
from audiodit import AudioDiTModel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model-dir',
                    default=os.path.join(EVAL, 'models', 'LongCat-AudioDiT-1B'))
    ap.add_argument('--umt5', default=os.path.join(EVAL, 'models', 'umt5-base'))
    ap.add_argument('--out', default='longcat-1b')
    ap.add_argument('--ref', default=r'reference_audio\ref_tight.wav')
    ap.add_argument('--ref-text',
                    default='')
    ap.add_argument('--segments', default=os.path.join(EVAL, 'scripts', 'segments.json'))
    ap.add_argument('--steps', type=int, default=16, help='ODE 步数 (nfe)')
    ap.add_argument('--cfg', type=float, default=4.0)
    ap.add_argument('--guidance', default='apg', choices=['cfg', 'apg'])
    ap.add_argument('--seed', type=int, default=1024)
    args = ap.parse_args()

    out_dir = os.path.join(EVAL, 'audio', args.out)
    os.makedirs(out_dir, exist_ok=True)
    with open(args.segments, encoding='utf-8') as f:
        segs = json.load(f)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    torch.manual_seed(args.seed)
    print(f'设备: {device}', flush=True)
    print(f'模型: {args.model_dir}', flush=True)
    print(f'参考: {args.ref}', flush=True)
    print(f'步数 nfe={args.steps} cfg={args.cfg} guidance={args.guidance}', flush=True)
    print('加载模型...', flush=True)
    t0 = time.time()
    model = AudioDiTModel.from_pretrained(args.model_dir).to(device)
    model.vae.to_half()
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(args.umt5)
    sr = model.config.sampling_rate
    full_hop = model.config.latent_hop
    max_duration = model.config.max_wav_duration
    print(f'模型就绪 ({time.time()-t0:.0f}s)  sr={sr} hop={full_hop} '
          f'max_wav_duration={max_duration}s', flush=True)

    ref_text = normalize_text(args.ref_text)
    prompt_wav = load_audio(args.ref, sr).unsqueeze(0)          # (1, T)
    with torch.no_grad():
        _, prompt_dur = model.encode_prompt_audio(prompt_wav.to(device))
    prompt_time = prompt_dur * full_hop / sr
    print(f'参考音频占 {prompt_dur} 帧 = {prompt_time:.2f}s '
          f'→ 单段可用预算 {max_duration - prompt_time:.2f}s', flush=True)

    rows = []
    t_start = time.time()
    for r in segs:
        text = normalize_text(r['text'])
        # 官方 inference.py 的时长估算逻辑
        dur_sec = approx_duration_from_text(text, max_duration=max_duration - prompt_time)
        approx_pd = approx_duration_from_text(ref_text, max_duration=max_duration)
        ratio = float(np.clip(prompt_time / approx_pd, 1.0, 1.5))
        dur_sec = dur_sec * ratio
        duration = int(dur_sec * sr // full_hop)
        duration = min(duration + prompt_dur, int(max_duration * sr // full_hop))

        full_text = f'{ref_text} {text}'
        inputs = tokenizer([full_text], padding='longest', return_tensors='pt')
        tb = time.time()
        try:
            with torch.no_grad():
                out = model(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask,
                    prompt_audio=prompt_wav,
                    duration=duration,
                    steps=args.steps,
                    cfg_strength=args.cfg,
                    guidance_method=args.guidance,
                )
            wav = out.waveform.squeeze().detach().cpu().numpy().astype(np.float32)
        except Exception as e:
            print(f'  seg{r["idx"]:02d} 生成失败: {type(e).__name__}: {e}', flush=True)
            rows.append(dict(idx=r['idx'], chars=r['chars'], ok=False, err=str(e)))
            torch.cuda.empty_cache()
            continue
        dt = time.time() - tb
        actual = len(wav) / sr
        # 注意: forward 里 pred_latent = pred_latent[:, prompt_dur:]，参考段已被切掉，
        # 所以输出波形就是"纯生成部分"，不要再减 prompt_time。
        p = os.path.join(out_dir, f'seg{r["idx"]:02d}.wav')
        sf.write(p, wav, sr)
        rows.append(dict(idx=r['idx'], chars=r['chars'], ok=True, sr=int(sr),
                         dur=round(actual, 3),
                         ask_sec=round(dur_sec, 3),
                         total_frames=int(duration),
                         prompt_frames=int(prompt_dur),
                         gen_sec=round(dt, 2), wav=os.path.basename(p)))
        print(f'  seg{r["idx"]:02d} {r["chars"]:3d}字 -> 请求生成 {dur_sec:5.2f}s / '
              f'实际产出 {actual:5.2f}s  用时 {dt:5.1f}s', flush=True)

    meta = dict(engine='longcat', model_dir=args.model_dir, ref=args.ref,
                ref_text=args.ref_text, steps=args.steps, cfg=args.cfg,
                guidance=args.guidance, prompt_dur_frames=int(prompt_dur),
                prompt_time=round(prompt_time, 3), max_wav_duration=max_duration,
                total_sec=round(time.time() - t_start, 1), rows=rows)
    with open(os.path.join(out_dir, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    ok = [r for r in rows if r.get('ok')]
    tot_audio = sum(r['dur'] for r in ok)
    tot_gen = sum(r['gen_sec'] for r in ok)
    print(f'\n完成: {len(ok)}/{len(rows)} 段, 产出音频合计 {tot_audio:.1f}s, '
          f'生成耗时 {tot_gen/60:.1f} min, RTF {tot_gen/max(tot_audio,1e-9):.2f}', flush=True)
    print(f'-> {out_dir}', flush=True)


if __name__ == '__main__':
    main()
