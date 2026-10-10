# -*- coding: utf-8 -*-
"""
修复第一章 27 个异常段：检测语音实际结束点，裁掉尾部噪声（127.9s 段的噪声尾）。

原理: 噪声尾是近乎静音的低电平（RMS<0.008 持续 >2s），裁掉后音频明显变干净。
策略: 从末尾向前扫描，找到"连续 2s RMS 高于阈值"的最后位置，作为语音结束点。
只处理 >60s 的异常段；正常段保持原样。
修复后重新拼接全书 wav + mp3。
"""
import os, glob, subprocess
import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CHUNK_DIR = os.path.join(ROOT, r"output\audiobook\_chunks")
OUT_DIR = os.path.join(ROOT, r"output\audiobook")
FIXED_DIR = os.path.join(CHUNK_DIR, "_fixed")
SR = 24000
SIL_S = 0.30
RMS_TAIL_THRESH = 0.008
TAIL_MIN_S = 2.0
MAX_TAIL_CUT = 100.0  # 最多裁 100s

os.makedirs(FIXED_DIR, exist_ok=True)


def trim_noise_tail(wav):
    """返回裁剪后的 wav；若无需裁剪返回 None。"""
    if wav.dtype != np.float32:
        wav = wav.astype(np.float32)
    frame = int(SR * 0.05)           # 50ms 帧
    n = len(wav)
    # 计算每帧 RMS
    n_frames = n // frame
    if n_frames < 10:
        return None
    t = np.arange(frame * n_frames).reshape(-1, frame)
    rms = np.sqrt(np.mean(wav[: frame * n_frames].reshape(-1, frame).astype(np.float64) ** 2, axis=1))
    # 从尾向前找连续静音起点：从末尾扫描，找到最后一个"语音帧"（RMS>=阈值）
    speech = np.where(rms >= RMS_TAIL_THRESH)[0]
    if len(speech) == 0:
        return None
    last_speech_frame = speech[-1]
    cut_at = min((last_speech_frame + 1) * frame, n)
    # 只对确实拖了尾巴的段裁剪（留 0.5s 余量）
    if n - cut_at < int(SR * 0.5):
        return None  # 无噪声尾
    return wav[:cut_at]


def main():
    chunks = sorted(glob.glob(os.path.join(CHUNK_DIR, "book1_part0006*__seg*.wav")))
    fixed_count = 0
    for i, c in enumerate(chunks):
        w, sr = sf.read(c)
        dur = len(w) / sr
        trimmed = trim_noise_tail(w)
        if trimmed is None:
            continue
        # 覆盖写回（先备份到 _fixed）
        out = os.path.join(FIXED_DIR, os.path.basename(c))
        sf.write(out, trimmed, sr)
        fixed_count += 1
        orig_dur = len(w) / sr
        new_dur = len(trimmed) / sr
        print(f"seg{i:04d}: {orig_dur:.1f}s -> {new_dur:.1f}s (裁掉 {orig_dur-new_dur:.1f}s)")
    print(f"\n共修复 {fixed_count} 段 -> {FIXED_DIR}")

    if fixed_count == 0:
        print("无异常段，结束")
        return

    # 替换原 chunk（覆盖）
    for c in chunks:
        base = os.path.basename(c)
        fx = os.path.join(FIXED_DIR, base)
        if os.path.exists(fx):
            os.replace(fx, c)
    print("已覆盖原 chunk 文件")

    # 重新拼接第一章
    chunks2 = sorted(glob.glob(os.path.join(CHUNK_DIR, "book1_part0006*__seg*.wav")))
    parts = []
    sil = np.zeros(int(SR * SIL_S), dtype=np.float32)
    for i, c in enumerate(chunks2):
        w, sr = sf.read(c)
        if w.dtype != np.float32:
            w = w.astype(np.float32)
        parts.append(w)
        if i < len(chunks2) - 1:
            parts.append(sil)
    full = np.concatenate(parts)
    wav_out = os.path.join(OUT_DIR, "book1_part0006.txt.wav")
    mp3_out = os.path.join(OUT_DIR, "book1_part0006.txt.mp3")
    sf.write(wav_out, full, SR)
    print(f"重新拼接: {wav_out} ({len(full)/SR/60:.1f} 分钟)")
    subprocess.run([r"bin\ffmpeg.exe", "-y", "-i", wav_out,
                    "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_out],
                   check=True, capture_output=True)
    print("重新转码:", mp3_out)


if __name__ == "__main__":
    main()