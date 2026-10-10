# -*- coding: utf-8 -*-
"""
合成防护模块：批量合成时对每段做时长校验，异常段自动重试/拆段/兜底。
被 batch_synth*.py 共享，防止生成撞 max_new_tokens=1600 上限产生 127.9s 噪声段。

BAD_DUR = 60.0   超过视为异常（正常段约 35-55s）
策略:
  1) 整段生成，校验时长；异常则重试 MAX_RETRY 次（换采样，大概率恢复正常）
  2) 仍不行 → 按逗号/顿号拆小为多片，逐片生成拼接
  3) 最后一搏整段生成，若还超长则截断到 FALLBACK_SEC 兜底
"""
import re
import numpy as np
import torch

BAD_DUR = 60.0
MAX_RETRY = 3
FALLBACK_SEC = 50.0
SR = 24000  # Qwen3-TTS 输出采样率


def _gen_one(model, prompt, text, max_new_tokens):
    wavs, sr = model.generate_voice_clone(
        text=text, language='Chinese', voice_clone_prompt=prompt,
        max_new_tokens=max_new_tokens,
    )
    w = wavs[0]
    if w.dtype != np.float32:
        w = w.astype(np.float32)
    return w, sr


def _clear_cache():
    torch.cuda.empty_cache()


def generate_guarded(model, prompt, seg, max_new_tokens=1600, verbose=True):
    """
    生成单个 segment（防护版）。返回 (wav, sr)。失败时返回 (None, None)。
    """
    # 1) 整段生成 + 校验，异常重试
    w, sr = None, None
    for attempt in range(MAX_RETRY):
        try:
            w, sr = _gen_one(model, prompt, seg, max_new_tokens)
        except Exception as e:
            if verbose:
                print(f'      gen 异常: {e}', flush=True)
            _clear_cache()
            w, sr = None, None
            continue
        if len(w) / sr <= BAD_DUR:
            return w, sr
        if verbose:
            print(f'      第{attempt+1}次仍超长({len(w)/sr:.0f}s)，重试', flush=True)
        _clear_cache()
        w, sr = None, None

    # 2) 拆段（按逗号/顿号/分号）逐片生成拼接
    pieces = [p for p in re.split(r'(?<=[，、；])', seg) if p.strip()]
    if len(pieces) > 1 and len(seg) > 40:
        out_pieces, ok = [], True
        for piece in pieces:
            pw = None
            for _ in range(3):
                try:
                    pw, psr = _gen_one(model, prompt, piece, max_new_tokens)
                except Exception:
                    _clear_cache()
                    pw = None
                    continue
                if len(pw) / psr <= BAD_DUR:
                    break
                _clear_cache()
                pw = None
            if pw is None:
                ok = False
                break
            out_pieces.append(pw)
        if ok and out_pieces:
            sil = np.zeros(int(SR * 0.15), dtype=np.float32)
            parts = []
            for k, p in enumerate(out_pieces):
                parts.append(p)
                if k < len(out_pieces) - 1:
                    parts.append(sil)
            return np.concatenate(parts), SR

    # 3) 兜底：最后一次整段生成，超长则截断
    try:
        w, sr = _gen_one(model, prompt, seg, max_new_tokens)
    except Exception:
        _clear_cache()
        return None, None
    if len(w) / sr > FALLBACK_SEC:
        w = w[: int(SR * FALLBACK_SEC)]
    return w, sr


def check_chunk_long(path, bad_dur=BAD_DUR):
    """检查单个 chunk 是否异常超长。返回时长（秒）。"""
    import soundfile as sf
    try:
        w, sr = sf.read(path)
        return len(w) / sr
    except Exception:
        return -1.0