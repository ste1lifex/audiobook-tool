# -*- coding: utf-8 -*-
"""
LongCat 电音是否可修：改变扩散 ODE 步数 (nfe) 重生成同一批句子，看 6-9k 高频平坦度
能否回升到接近人声基准（人声 ~0.16-0.30）。

同时测 cfg/guidance 强度的影响。跑在 envs\\longcat。
"""
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

OUT = os.path.join(EVAL, 'audio', '_longcat_nfe')
os.makedirs(OUT, exist_ok=True)
REF = r'reference_audio\ref_tight.wav'
REF_TEXT = ''
N_SEG = 4

with open(os.path.join(EVAL, 'scripts', 'segments.json'), encoding='utf-8') as f:
    segs = json.load(f)[:N_SEG]


def buzz_metrics(w, sr):
    import librosa
    w = np.asarray(w, dtype=np.float32)
    S = np.abs(librosa.stft(w, n_fft=1024, hop_length=256)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    e = S.sum(axis=0)
    lvl = np.percentile(e, 90)
    sel = e >= max(np.percentile(e, 50), 0.12 * lvl)
    Sa = S[:, sel]
    out = {}
    for lo, hi, nm in ((4000, 6000, '4-6k'), (6000, 9000, '6-9k')):
        m = (freqs >= lo) & (freqs < hi)
        seg = Sa[m, :]
        out[nm] = float(np.mean(np.exp(np.mean(np.log(seg + 1e-10), axis=0))
                                / (np.mean(seg, axis=0) + 1e-10)))
    m = freqs >= 5000
    Sh = Sa[m, :]
    out['hf'] = float(np.mean(np.exp(np.mean(np.log(Sh + 1e-10), axis=0))
                              / (np.mean(Sh, axis=0) + 1e-10)))
    out['centroid'] = float((freqs[:, None] * Sa).sum() / (Sa.sum() + 1e-12))
    return out


device = torch.device('cuda')
model = AudioDiTModel.from_pretrained(
    os.path.join(EVAL, 'models', 'LongCat-AudioDiT-1B')).to(device)
model.vae.to_half()
model.eval()
tokenizer = AutoTokenizer.from_pretrained(os.path.join(EVAL, 'models', 'umt5-base'))
sr = model.config.sampling_rate
hop = model.config.latent_hop
maxdur = model.config.max_wav_duration

rt = normalize_text(REF_TEXT)
prompt_wav = load_audio(REF, sr).unsqueeze(0)
with torch.no_grad():
    _, pdur = model.encode_prompt_audio(prompt_wav.to(device))
ptime = pdur * hop / sr
print(f'参考占 {ptime:.2f}s，人声基准 6-9k平坦≈0.16-0.30 高频平坦≈0.11-0.29\n', flush=True)

CONFIGS = [('nfe16', 16, 4.0, 'apg'), ('nfe32', 32, 4.0, 'apg'),
           ('nfe64', 64, 4.0, 'apg'), ('nfe32_cfg2', 32, 2.0, 'apg'),
           ('nfe32_cfg', 32, 4.0, 'cfg')]
print(f'{"配置":<12}{"6-9k平坦":>10}{"4-6k平坦":>10}{"高频平坦":>10}'
      f'{"质心Hz":>9}{"均耗时":>9}', flush=True)
rows = []
for label, nfe, cfg, gm in CONFIGS:
    ms, times = [], []
    for k, r in enumerate(segs):
        text = normalize_text(r['text'])
        ds = approx_duration_from_text(text, max_duration=maxdur - ptime)
        ap = approx_duration_from_text(rt, max_duration=maxdur)
        ratio = float(np.clip(ptime / ap, 1.0, 1.5))
        ds *= ratio
        dur = min(int(ds * sr // hop) + pdur, int(maxdur * sr // hop))
        ins = tokenizer([f'{rt} {text}'], padding='longest', return_tensors='pt')
        t0 = time.time()
        with torch.no_grad():
            o = model(input_ids=ins.input_ids, attention_mask=ins.attention_mask,
                      prompt_audio=prompt_wav, duration=dur, steps=nfe,
                      cfg_strength=cfg, guidance_method=gm)
        times.append(time.time() - t0)
        w = o.waveform.squeeze().detach().cpu().numpy().astype(np.float32)
        p = os.path.join(OUT, f'{label}_seg{k:02d}.wav')
        sf.write(p, w, sr)
        ms.append(buzz_metrics(w, sr))
    avg = {k: float(np.mean([m[k] for m in ms])) for k in ms[0]}
    rows.append((label, avg, float(np.mean(times))))
    print(f'{label:<12}{avg["6-9k"]:>10.4f}{avg["4-6k"]:>10.4f}{avg["hf"]:>10.4f}'
          f'{avg["centroid"]:>9.0f}{np.mean(times):>8.1f}s', flush=True)

with open(os.path.join(EVAL, 'reports', 'longcat_nfe.json'), 'w', encoding='utf-8') as f:
    json.dump([dict(label=l, metrics=m, sec=t) for l, m, t in rows], f,
              ensure_ascii=False, indent=1)
print(f'\n-> {OUT}')
