# -*- coding: utf-8 -*-
"""
有声书生成工具 - 配置模块

路径解析规则（可移植设计，换电脑不用改代码）：
  每一条外部资源按以下顺序查找——
    ① 环境变量（见下方 AUDIOBOOK_* 名称）
    ② **项目内**约定位置：`<项目根>/models`、`/bin`、`/reference_audio`
       （单仓库布局：一切都在 audiobook_tool/ 里，这是默认情形）
    ③ **发行包内**约定位置：`<包根>/models` 等
       （打包布局：audiobook_portable/audiobook_tool + audiobook_portable/models）
    ④ 系统 PATH（ffmpeg 走 shutil.which）
    ⑤ 开发机兜底根目录（**可选**：设 AUDIOBOOK_DEV_ROOT 才启用）

仓库里不存在任何硬编码的机器路径；⑤ 仅用于开发期把资源留在仓库外时兜底。
"""
import os
import shutil

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGE_ROOT = os.path.dirname(PROJECT_ROOT)          # 发行包根目录（若为打包布局）
OUTPUT_BASE = os.path.join(PROJECT_ROOT, 'output')      # 所有生成结果根目录（书库）
INPUT_DIR = os.path.join(PROJECT_ROOT, 'input')         # 输入目录：把电子书丢这里即可被识别
WORK_ROOT = os.path.join(PROJECT_ROOT, 'work')          # 中间文件（提取文本等）


def _env(name):
    """读环境变量覆盖值（空串视为未设置）。"""
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _proj(*parts):
    """项目内约定位置（单仓库布局，优先）"""
    return os.path.join(PROJECT_ROOT, *parts)


def _pkg(*parts):
    """发行包内约定位置（打包布局）"""
    return os.path.join(PACKAGE_ROOT, *parts)


def _first_file(*cands):
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _first_dir(*cands):
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return None


# 开发机兜底根目录（**可选**）。设了 AUDIOBOOK_DEV_ROOT 时，程序会在
# <DEV_ROOT>/Qwen3-TTS/pretrained_models/... 这类老位置上再找一次；
# 不设就完全跳过，因此仓库里不会有任何硬编码的机器路径。
_DEV = _env('AUDIOBOOK_DEV_ROOT')


def _dev(*parts):
    """开发机兜底位置；未设 AUDIOBOOK_DEV_ROOT 时返回 None（不参与查找）。"""
    return os.path.join(_DEV, *parts) if _DEV else None


# ---- Qwen3-TTS 模型权重目录（约 2.3GB，setup.ps1 会自动下载）----
MODEL_DIR = (_env('AUDIOBOOK_MODEL_DIR')
             or _first_dir(_proj('models', 'Qwen3-TTS-12Hz-0.6B-Base'),
                           _pkg('models', 'Qwen3-TTS-12Hz-0.6B-Base'),
                           _dev('Qwen3-TTS', 'pretrained_models',
                                'Qwen3-TTS-12Hz-0.6B-Base'))
             or _proj('models', 'Qwen3-TTS-12Hz-0.6B-Base'))

# 默认克隆参考音频（用户自备，通常有版权，不入库）
# ref_clip_full.wav 的问题：8.60s 里只有 4.60s 真实语音、静音占 47%，
# 且是三段独立台词拼在一起。ICL 克隆模式下模型直接以这段音频的 codec token 为条件，
# 参考不干净 → 音色不稳（实测会出现"整段换人""段首换人"）。
# ref_tight.wav 由 build_tight_ref.py 生成：压缩静音、保留同一段语音，6.08s。
# 实测（6 段对照，统一声纹锚点）：相似度 0.830 vs 0.818，谱平坦度 0.003 vs 0.015。
DEFAULT_REF_AUDIO = (_env('AUDIOBOOK_REF_AUDIO')
                     or _first_file(_proj('reference_audio', 'ref_tight.wav'),
                                    _pkg('reference_audio', 'ref_tight.wav'),
                                    _dev('reference_audio', 'ref_tight.wav'))
                     or _proj('reference_audio', 'ref_tight.wav'))


def _default_ref_text():
    """参考音频对应的文本 —— 必须与参考音频的**实际内容一致**。

    优先级：AUDIOBOOK_REF_TEXT 环境变量 → reference_audio/ref_text.txt → 空串。
    参考音频是使用者自备的版权素材，所以这里**不内置任何具体台词**；
    文本不匹配会让 ICL 克隆明显失真（音色不稳、整段换人）。
    """
    v = _env('AUDIOBOOK_REF_TEXT')
    if v:
        return v
    p = _first_file(_proj('reference_audio', 'ref_text.txt'),
                    _pkg('reference_audio', 'ref_text.txt'))
    if p:
        try:
            # utf-8-sig: 兼容记事本/OneDrive 可能写入的 BOM
            with open(p, encoding='utf-8-sig') as f:
                t = f.read().strip()
            if t:
                return t
        except OSError:
            pass
    return ''


DEFAULT_REF_TEXT = _default_ref_text()

# ---- TTS 采样参数 ----
# 库的硬默认是 do_sample=True / temperature=0.9 / top_p=1.0 / repetition_penalty=1.05，
# 那是"追求多样性"的探索配置；对"上千段都要像同一个人"的有声书是反向的。
# 6 段对照实测（CAM++ 声纹相似度越高越像参考音色，谱平坦度越低越干净）：
#   库默认 temp0.9/top_p1.0/1600 : 0.797 ± 0.036, 最低 0.736, 谱平坦度 0.014,
#                                  且出现 127.9s 撞顶失控段（就是"杂音"的来源）
#   temp0.7/top_p0.9/2048        : 0.780 ± 0.029  ← 更差，不用
#   temp0.5/top_p0.9/rep1.15/2048: 0.830 ± 0.012, 最低 0.815, 谱平坦度 0.003 ← 采用
#   do_sample=False 贪心          : -0.053（完全不是参考音色）+ 谱平坦度 0.353，禁用
TEMPERATURE = 0.5
TOP_P = 0.9
REPETITION_PENALTY = 1.15

# TTS 参数
BATCH_SIZE = 4               # 批量推理 4 段比逐段快 4.5 倍（26s/段 vs 116s/段），
                             # 且质量无差别（实测 0.821±0.011 vs 0.830±0.012）
MAX_NEW_TOKENS = 2048        # 恢复库默认；原先 1600 会让长段撞顶被截断→产生噪声尾
# 段长上限：205 → 105。依据是三路横向评测（同一批连续段落、同一参考音频、
# 同一套采样参数）中，105 字分段 + 0.45s 段间静音的 0.6B 输出被听感判定为
# "语速和声音控制都比较好、没有奇怪杂音"，并采纳为基准；205 字版本此前被指"有点赶"。
# 代价：段数约翻倍（第六章 249 → 526 段），但单段更短也更不容易漂移。
MAX_CHARS_PER_SEG = 105

# 切分算法版本。**任何切分逻辑的改动都必须 +1**，因为它会被算进段缓存指纹
# （engine.chunk_cache_key）。否则"上限没变但算法变了"会导致旧段被静默复用，
# 出现音频与文本错位 —— 与 205→105 那次是同一类 bug。
#   v1: 原实现（二级切分只在首段生效，标点集合只有中文标点）
#   v2: 原子片段 + 贪心合并；扩展标点（/ \ ＞ ][ 等）；强制切分兜底
SEG_VERSION = 2

BAD_DUR = 70.0               # 段时长上限（2048 token @12Hz ≈ 170s，留足余量）
SEG_SILENCE_SEC = 0.45       # 段间静音（评测基准值；原 0.30）
SAMPLE_RATE = 24000

# ---- 段级质检（quality.py）----
# 原来只用"时长<=60s"判异常，抓不到两类真实缺陷：
#   ① 整段是别人的声音但时长/音量都正常（实测占 0.3%，相似度 0.32~0.59）
#   ② 段首或局部换人、之后自己找回（整段平均看不出来）
GATE_ENABLED = True
SIM_MIN = 0.62               # 整体声纹相似度下限（正常段 P1=0.711，跑偏段 0.32~0.59）
WIN_SIM_MIN = 0.45           # 段内 3s 滑窗最低相似度（抓"局部换人"）
WIN_RMS_FLOOR = 0.010        # 滑窗只用"真有语音能量"的窗，避免换气/停顿造成假阳性
FLATNESS_MAX = 0.08          # 谱平坦度上限（正常 ~0.014，退化噪声段 0.21~0.35）
# 电音检测：6-9kHz 谱平坦度下限。自然语音该频段含大量非周期成分（气声/齿音/摩擦音），
# 被过度谐波化后听感即"电音"。实测分布（同一生产函数，逐段）：
#     真实人声            0.18 ~ 0.23
#     本项目旧书稿(205字)   均值 0.227  最低 0.188  (60 段)
#     Qwen3-0.6B 全章526段  均值 0.113  最低 0.052  P0.5 0.060
#     Qwen3-1.7B(12段)     均值 0.105  最低 0.053
#     LongCat-AudioDiT-1B  均值 0.027  最高 0.032  ← 听感判定"电音严重"
# 取 0.04 = 两侧的几何中点 (0.0321*0.0516)^0.5 = 0.0407，两边各约 1.25 倍余量。
# 注意该缺陷与推理设置无关（nfe 16/32/64、apg/cfg 均约 0.025），属模型固有特性。
HF_TONALITY_MIN = 0.04
HF_TONALITY_MIN_DUR = 3.0    # 只对 >=3s 的段查电音；短段该谱统计量方差大（实测最低值
                             # 0.0516 就出现在一个 5.3s 段上）
GATE_MAX_ATTEMPTS = 3        # 一段最多重生成次数；全不达标时保留最好的一次

# 说话人验证模型（CAM++，192 维声纹），质检门用它判断"像不像参考音色"。
# 取自 CosyVoice2-0.5B 仓库；本发行包已随附于 models/campplus.onnx（27MB）。
CAMPPLUS_ONNX = (_env('AUDIOBOOK_CAMPPLUS')
                 or _first_file(_proj('models', 'campplus.onnx'),
                                _pkg('models', 'campplus.onnx'),
                                _dev('CosyVoice', 'pretrained_models',
                                     'CosyVoice2-0.5B', 'campplus.onnx'))
                 or _proj('models', 'campplus.onnx'))

# 注：曾有一套"停顿校正"（pacing.py，把句间停顿按分位数拉长），已删除。
# 原因是它基于一个错误诊断——以为"赶"是停顿太短；实测主因是**段太长**。
# 详见发行包内 tts_eval/README.md 的踩坑记录。

# ---- 音频工具 ffmpeg / ffprobe ----
# 查找顺序: 环境变量 -> 项目内 bin/ -> 发行包内 bin/ -> 系统 PATH


def _tool(exe, env_name):
    p = _env(env_name)
    if p and os.path.isfile(p):
        return p
    p = _first_file(_proj('bin', exe), _pkg('bin', exe))
    if p:
        return p
    w = shutil.which(exe.replace('.exe', ''))
    return w or exe


FFMPEG = _tool('ffmpeg.exe', 'AUDIOBOOK_FFMPEG')
FFPROBE = _tool('ffprobe.exe', 'AUDIOBOOK_FFPROBE')

# 支持的书格式
SUPPORTED_EXT = {'.epub', '.mobi', '.azw3', '.azw', '.txt'}

for d in (OUTPUT_BASE, INPUT_DIR, WORK_ROOT):
    os.makedirs(d, exist_ok=True)