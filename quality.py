# -*- coding: utf-8 -*-
"""
段级质检：判断一段合成音频"像不像参考音色"、"有没有退化成噪声"。

为什么要这个模块：
  原来的 engine.py 只用"时长 <= 60s"来判断一段是否正常，抓不到两类真实缺陷：
    ① 音色跑偏——整段是另一个人的声音，但时长/音量都正常（实测占 0.3%）
    ② 模型退化——撞 token 上限后产出大段噪声（谱平坦度从 0.014 飙到 0.21）
  本模块用 CAM++ 声纹模型 + 谱平坦度把这两类缺陷变成可判定指标。

实测标定（ref_clip_full 为锚点，1782 段）：
    正常段 sim 中位数 0.804，P1=0.711
    跑偏段 sim = 0.321 / 0.365 / 0.402 / 0.478 / 0.589
    正常段谱平坦度 ~0.014；退化噪声段 0.214
  故取 SIM_MIN=0.62、FLATNESS_MAX=0.08 作为门限。
"""
import os

import numpy as np

# 默认模型路径（可移植）：环境变量 -> 项目内 models/ -> 发行包内 models/ -> 可选开发机兜底
def _default_campplus():
    env = os.environ.get('AUDIOBOOK_CAMPPLUS')
    if env and os.path.isfile(env):
        return env
    proj = os.path.dirname(os.path.abspath(__file__))          # audiobook_tool/
    pkg = os.path.dirname(proj)                                # 发行包根（打包布局）
    cands = [os.path.join(proj, 'models', 'campplus.onnx'),
             os.path.join(pkg, 'models', 'campplus.onnx')]
    dev = os.environ.get('AUDIOBOOK_DEV_ROOT')
    if dev:
        cands.append(os.path.join(dev, 'CosyVoice', 'pretrained_models',
                                  'CosyVoice2-0.5B', 'campplus.onnx'))
    for c in cands:
        if os.path.isfile(c):
            return c
    return os.path.join(proj, 'models', 'campplus.onnx')


CAMPPLUS_ONNX = _default_campplus()
SR_FBANK = 16000


class SpeakerScorer:
    """CAM++ 声纹向量提取（192 维），用于算"像不像"。"""

    _sess = None

    def __init__(self, model_path=CAMPPLUS_ONNX):
        import onnxruntime as ort
        import torch
        import torchaudio
        if not os.path.exists(model_path):
            raise FileNotFoundError(f'找不到 CAM++ 模型: {model_path}')
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(model_path, so,
                                         providers=['CPUExecutionProvider'])
        self.torch = torch
        self.torchaudio = torchaudio

    def embed(self, wav, sr):
        t = self.torch
        w = t.from_numpy(np.ascontiguousarray(wav, dtype=np.float32)).unsqueeze(0)
        if sr != SR_FBANK:
            w = self.torchaudio.functional.resample(w, sr, SR_FBANK)
        feat = self.torchaudio.compliance.kaldi.fbank(
            w, num_mel_bins=80, dither=0.0, sample_frequency=SR_FBANK)
        feat = feat - feat.mean(dim=0, keepdim=True)
        out = self.sess.run(None, {'input': feat.numpy()[np.newaxis].astype(np.float32)})
        e = out[0][0]
        return e / (np.linalg.norm(e) + 1e-9)


def spectral_flatness(wav, sr, top_percentile=40):
    """
    谱平坦度：几何均值/算术均值。越接近 1 越像噪声。
    只统计能量较高的帧，避免静音帧拉偏。

    注意方向性：这个指标**不是越低越好**。自然语音的高频本来就含大量非周期成分
    （气声/齿音/摩擦音）。平坦度远低于人声 = 高频被压成周期性嗡鸣 = "电音"。
    判断是否异常要用 hf_tonality() 而非本函数。
    """
    import librosa
    w = np.asarray(wav, dtype=np.float32)
    if len(w) < 1024:
        return 0.0
    S = np.abs(librosa.stft(w, n_fft=1024, hop_length=512)) ** 2
    e = S.sum(axis=0)
    if e.size == 0:
        return 0.0
    act = e >= np.percentile(e, top_percentile)
    if not act.any():
        return 0.0
    Sa = S[:, act]
    gm = np.exp(np.mean(np.log(Sa + 1e-10), axis=0))
    am = np.mean(Sa, axis=0)
    return float(np.mean(gm / (am + 1e-10)))


def hf_tonality(wav, sr, lo=6000, hi=9000):
    """
    6-9kHz 频段的谱平坦度 —— 用来抓"电音"（高频被过度谐波化）。

    实测基准（同一函数、24kHz）:
        真实人声（参考音色原声 6 个片段）  0.16 ~ 0.30
        本项目已生成书稿 80 段          均值 0.223，最小 0.167
        Qwen3-0.6B 评测输出             0.124
        Qwen3-1.7B 评测输出             0.105
        LongCat-AudioDiT-1B（被判电音）  0.027   ← 人声的 1/7
    即：该值远低于人声 = 高频缺少气声/齿音这类非周期成分，听感为周期性嗡鸣。
    在 80 段现有书稿上零误报（无一低于 0.10）。
    """
    import librosa
    w = np.asarray(wav, dtype=np.float32)
    if len(w) < 1024:
        return 1.0
    S = np.abs(librosa.stft(w, n_fft=1024, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    e = S.sum(axis=0)
    if e.size == 0:
        return 1.0
    lvl = np.percentile(e, 90)
    sel = e >= max(np.percentile(e, 50), 0.12 * lvl)
    if sel.sum() < 4:
        return 1.0
    Sa = S[:, sel]
    m = (freqs >= lo) & (freqs < hi)
    if not m.any():
        return 1.0
    seg = Sa[m, :]
    gm = np.exp(np.mean(np.log(seg + 1e-10), axis=0))
    am = np.mean(seg, axis=0)
    return float(np.mean(gm / (am + 1e-10)))


class SegmentGate:
    """
    段级质检门。持有参考音色的声纹向量，对每段产出 (是否合格, 原因, 指标)。
    线程内只做一次参考嵌入；之后每段约 0.1-0.3s CPU，相对生成耗时可忽略。
    """

    def __init__(self, ref_audio, sim_min=0.62, flatness_max=0.08,
                 bad_dur=70.0, scorer=None,
                 win_sim_min=0.45, win_sec=3.0, win_hop=1.5, win_rms_floor=0.010,
                 hf_tonality_min=0.04, hf_tonality_min_dur=3.0):
        import soundfile as sf
        self.sim_min = sim_min
        self.flatness_max = flatness_max
        self.bad_dur = bad_dur
        # 段内滑窗：抓"整段平均正常、但局部换人"的情况（平均会把局部缺陷抹平）
        self.win_sim_min = win_sim_min
        self.win_sec = win_sec
        self.win_hop = win_hop
        self.win_rms_floor = win_rms_floor
        # 6-9k 高频平坦度下限：远低于人声 = 电音（见 hf_tonality 的实测基准）
        self.hf_tonality_min = hf_tonality_min
        self.hf_tonality_min_dur = hf_tonality_min_dur
        self.scorer = scorer or SpeakerScorer()
        w, sr = sf.read(ref_audio)
        if w.ndim > 1:
            w = w.mean(axis=1)
        self.ref_emb = self.scorer.embed(w, sr)
        self.ref_audio = ref_audio

    def _window_sims(self, w, sr):
        """
        滑窗声纹相似度。只统计"真有语音能量"的窗：
        绝对下限 + 相对该段语音中位数的下限，避免把换气/停顿窗当成人声判据
        （这类窗的声纹不可靠，会算出 sim≈0 的假阳性）。
        返回 (最低值, 对应时间)。
        """
        win, hop = int(self.win_sec * sr), int(self.win_hop * sr)
        if len(w) < win:
            win, hop = len(w), max(1, len(w) // 2)
        # 先算所有窗的能量，取中位数作为该段的"正常语音电平"
        rms_all = []
        for s in range(0, max(1, len(w) - win + 1), hop):
            seg = w[s:s + win]
            if len(seg) < int(0.5 * sr):
                break
            rms_all.append(float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))))
        if not rms_all:
            return None, 0.0
        med = float(np.median(rms_all))
        floor = max(self.win_rms_floor, 0.5 * med)

        worst, worst_t = None, 0.0
        for k, s in enumerate(range(0, max(1, len(w) - win + 1), hop)):
            seg = w[s:s + win]
            if len(seg) < int(0.5 * sr) or k >= len(rms_all):
                break
            if rms_all[k] < floor:        # 换气/停顿窗跳过
                continue
            e = self.scorer.embed(seg, sr)
            sim = float(np.dot(e, self.ref_emb))
            if worst is None or sim < worst:
                worst, worst_t = sim, s / sr
        return worst, worst_t

    def check(self, wav, sr):
        """返回 (ok, reason, metrics)。reason 为空表示通过。"""
        w = np.asarray(wav, dtype=np.float32)
        dur = len(w) / sr
        reasons = []
        if dur > self.bad_dur:
            reasons.append(f'超长{dur:.0f}s')
        m = {'dur': dur}
        # 极短段无法可靠算声纹，只查时长
        if dur >= 1.0:
            e = self.scorer.embed(w, sr)
            m['sim'] = float(np.dot(e, self.ref_emb))
            if m['sim'] < self.sim_min:
                reasons.append(f'音色跑偏{m["sim"]:.2f}')
            if dur >= self.win_sec:
                wsim, wt = self._window_sims(w, sr)
                if wsim is not None:
                    m['win_sim'] = wsim
                    m['win_at'] = wt
                    if wsim < self.win_sim_min:
                        reasons.append(f'局部换人{wsim:.2f}@{wt:.0f}s')
            m['flatness'] = spectral_flatness(w, sr)
            if m['flatness'] > self.flatness_max:
                reasons.append(f'噪声退化{m["flatness"]:.2f}')
            # 电音：6-9kHz 被过度谐波化（人声 0.18~0.23，电音 ~0.03）
            # 短段该谱统计量方差大，故设最短时长门槛
            if dur >= self.hf_tonality_min_dur:
                m['hf_tonality'] = hf_tonality(w, sr)
                if m['hf_tonality'] < self.hf_tonality_min:
                    reasons.append(f'疑似电音{m["hf_tonality"]:.3f}')
        return (not reasons), '; '.join(reasons), m

    def score(self, wav, sr):
        """只给分不判定（用于从多次尝试里挑最好的一次）。"""
        ok, _, m = self.check(wav, sr)
        return m
