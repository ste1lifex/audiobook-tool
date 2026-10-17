# -*- coding: utf-8 -*-
"""
合成引擎：Qwen3-TTS 克隆音色 → 逐章合成 → 断点续跑 → 命名规律化输出。

输出目录结构（以书名为根）:
  output/<书名>/
    _chunks/<书名>_ch<NN>_seg<NNNNN>.wav     # 段级断点缓存
    <书名>_ch<NN>_<章名>.mp3                  # 每章音频
    <书名>_全书.mp3                            # 整本合并
    <书名>_播放清单.m3u                        # 播放清单
    task_state.json                            # 断点状态（可续跑）
"""
import glob
import json
import os
import re
import subprocess
import threading
import time

import numpy as np
import soundfile as sf
import torch

import config

# 全局单例（避免重复加载模型）
_model = None
_prompt = None
_load_lock = threading.Lock()


def get_model():
    global _model, _prompt
    with _load_lock:
        if _model is not None:
            return _model, _prompt
        from qwen_tts import Qwen3TTSModel
        use_cuda = torch.cuda.is_available()
        print(f'>> 加载模型 (cuda={use_cuda}) ...', flush=True)
        _model = Qwen3TTSModel.from_pretrained(
            config.MODEL_DIR,
            device_map='cuda:0' if use_cuda else 'cpu',
            dtype=torch.bfloat16 if use_cuda else torch.float32,
        )
        # 一次性构建克隆 prompt（复用）。但默认音色只有「文件存在且文本非空」时才建：
        # 发行包不带参考音频，缺省时这里必须放行，让用户上传的那段在 set_reference 里生效，
        # 否则全新用户第一次点开始生成会直接崩在 soundfile 打不开默认文件上。
        if os.path.isfile(config.DEFAULT_REF_AUDIO) and (config.DEFAULT_REF_TEXT or '').strip():
            _prompt = _model.create_voice_clone_prompt(
                ref_audio=config.DEFAULT_REF_AUDIO,
                ref_text=config.DEFAULT_REF_TEXT,
                x_vector_only_mode=False,
            )
        else:
            _prompt = None
        print('>> 模型就绪', flush=True)
        return _model, _prompt


# 全新用户（发行包按版权要求不带任何参考音频/文本）第一次生成时，缺什么必须
# 用中文说清楚「下一步点哪里」，而不是让底层库抛 soundfile / 英文 ValueError。
REF_AUDIO_MISSING_MSG = (
    '还没有可用的参考音色：请先在「音色设置」里上传一段参考音频'
    '（5~10 秒、单人、干净、已剪掉静音），或把音频放到 reference_audio\\ref_tight.wav。'
)
REF_TEXT_MISSING_MSG = (
    '「参考音色对应文本」为空：请填写与参考音频逐字一致的文字'
    '（在「音色设置」文本框里填，或写进 reference_audio\\ref_text.txt）。'
)


def resolve_reference(ref_audio=None, ref_text=None):
    """解析并校验本次实际使用的参考音频/文本，返回 (绝对路径, 文本)。

    顺序：本次传入 → 默认位置（reference_audio/ref_tight.wav）。
    缺音频或文本时抛 RuntimeError，消息是可直接照做的中文操作提示。
    """
    audio = ref_audio.strip() if isinstance(ref_audio, str) else ref_audio
    if not audio or not os.path.isfile(audio):
        audio = config.DEFAULT_REF_AUDIO
    if not os.path.isfile(audio):
        raise RuntimeError(REF_AUDIO_MISSING_MSG)
    text = (ref_text or config.DEFAULT_REF_TEXT or '').strip()
    if not text:
        raise RuntimeError(REF_TEXT_MISSING_MSG)
    return os.path.abspath(audio), text


def set_reference(ref_audio, ref_text):
    """更换克隆参考音频（调用前需重载模型 prompt）。返回新的 prompt。"""
    global _model, _prompt
    with _load_lock:
        if _model is None:
            return _prompt
        if not (ref_text or '').strip():
            raise RuntimeError(REF_TEXT_MISSING_MSG)
        _prompt = _model.create_voice_clone_prompt(
            ref_audio=ref_audio, ref_text=ref_text, x_vector_only_mode=False)
        print('>> 参考音频已更新', flush=True)
        return _prompt


# 切分用的标点分级（SEG_VERSION 变更时必须同步 +1）
#   一级 SENT   ：句末，一定可以断
#   二级 CLAUSE ：句中停顿
#   三级 LAYOUT ：排版/分隔符 —— 有些段落（如咏唱体、机器独白）不用中文标点，
#                而用 / \ ＞ ][ —— 这类必须也能断，否则整段切不开
_SEG_SENT = '。！？；…!?;'
_SEG_CLAUSE = '，、：,'
_SEG_LAYOUT = '/\\＞><[]【】〔〕—·|'


def _hard_split(p, mx):
    """四级兜底：按字数硬切，尽量在标点后断开，避免切在词中间。"""
    out = []
    while len(p) > mx:
        cut = 0
        for i in range(mx, max(1, mx - 20), -1):
            if p[i - 1] in _SEG_SENT or p[i - 1] in _SEG_CLAUSE or p[i - 1] in _SEG_LAYOUT:
                cut = i
                break
        if not cut:
            cut = mx
        out.append(p[:cut])
        p = p[cut:]
    if p:
        out.append(p)
    return out


def _split_overlong(p, mx):
    """把一个超过 mx 的片段拆成若干 <= mx 的片段（二级→三级→四级）。"""
    out = []
    for s in re.split(r'(?<=[%s])' % re.escape(_SEG_CLAUSE), p):
        if not s:
            continue
        if len(s) <= mx:
            out.append(s)
            continue
        for t in re.split(r'(?<=[%s])' % re.escape(_SEG_LAYOUT), s):
            if not t:
                continue
            if len(t) <= mx:
                out.append(t)
            else:
                out.extend(_hard_split(t, mx))
    return out


def split_segments(text, max_chars=config.MAX_CHARS_PER_SEG):
    """
    按标点切段并合并到 <= max_chars。

    实现要点（SEG_VERSION=2 起）：
      先把整篇切成**每个都不超过上限的原子片段**，再贪心合并。
      这样"超长片段"在结构上不可能出现——v1 的问题正是二级切分只在
      buf 为空时执行，导致后续超长句被整段吐出（实测切出过 1301 字 / 163.8 秒的段，
      撞上 max_new_tokens 上限被截断，残余还挤成一个 8 字的垃圾段）。
    """
    mx = int(max_chars or config.MAX_CHARS_PER_SEG)
    text = re.sub(r'\s+', '', text)
    # 一级：句末标点
    pieces = []
    for p in re.split(r'(?<=[%s])' % re.escape(_SEG_SENT), text):
        if not p:
            continue
        if len(p) <= mx:
            pieces.append(p)
        else:
            pieces.extend(_split_overlong(p, mx))
    # 贪心合并相邻短片段
    segs, buf = [], ''
    for p in pieces:
        if len(buf) + len(p) <= mx:
            buf += p
        else:
            if buf:
                segs.append(buf)
            buf = p
    if buf:
        segs.append(buf)
    return [s for s in segs if s.strip()]


def _clear_cache():
    torch.cuda.empty_cache()


# ============ 采样参数 ============
# 库默认 temp=0.9 / top_p=1.0 / do_sample=True 是追求多样性的探索配置；
# 对"上千段都要像同一个人"的有声书是反向的（实测见 config.py 注释）。
def _gen_kwargs():
    kw = {}
    if getattr(config, 'TEMPERATURE', None) is not None:
        kw['temperature'] = config.TEMPERATURE
    if getattr(config, 'TOP_P', None) is not None:
        kw['top_p'] = config.TOP_P
    if getattr(config, 'REPETITION_PENALTY', None) is not None:
        kw['repetition_penalty'] = config.REPETITION_PENALTY
    return kw


def _gen_one(model, prompt, text, max_new_tokens=config.MAX_NEW_TOKENS):
    wavs, sr = model.generate_voice_clone(
        text=text, language='Chinese', voice_clone_prompt=prompt,
        max_new_tokens=max_new_tokens, **_gen_kwargs(),
    )
    w = wavs[0]
    if w.dtype != np.float32:
        w = w.astype(np.float32)
    return w, sr


# ============ 段级质检门 ============
_gate = None
_gate_key = None


def get_gate(ref_audio):
    """
    取（或构建）质检门。参考音色变了就重建。
    质检依赖 onnxruntime/CAM++；不可用时降级为"只查时长"，不影响主流程。
    """
    global _gate, _gate_key
    if not getattr(config, 'GATE_ENABLED', True):
        return None
    if _gate is not None and _gate_key == ref_audio:
        return _gate
    try:
        import quality
        _gate = quality.SegmentGate(
            ref_audio,
            sim_min=getattr(config, 'SIM_MIN', 0.62),
            win_sim_min=getattr(config, 'WIN_SIM_MIN', 0.45),
            win_rms_floor=getattr(config, 'WIN_RMS_FLOOR', 0.010),
            flatness_max=getattr(config, 'FLATNESS_MAX', 0.08),
            hf_tonality_min=getattr(config, 'HF_TONALITY_MIN', 0.04),
            hf_tonality_min_dur=getattr(config, 'HF_TONALITY_MIN_DUR', 3.0),
            bad_dur=getattr(config, 'BAD_DUR', 70.0),
        )
        _gate_key = ref_audio
        print(f'>> 质检门已就绪（参考 {os.path.basename(ref_audio)}，'
              f'sim>={_gate.sim_min} 窗>={_gate.win_sim_min} '
              f'flat<={_gate.flatness_max} 电音下限>={_gate.hf_tonality_min}）',
              flush=True)
        return _gate
    except Exception as e:
        print(f'!! 质检门不可用({e})，降级为仅查时长', flush=True)
        _gate = 'disabled'
        _gate_key = ref_audio
        return None


def _gate_check(wav, sr, gate):
    """返回 (ok, reason, metrics)；gate 不可用时只查时长。"""
    dur = len(wav) / sr
    if gate in (None, 'disabled'):
        ok = dur <= getattr(config, 'BAD_DUR', 70.0)
        return ok, ('' if ok else f'超长{dur:.0f}s'), {'dur': dur}
    return gate.check(wav, sr)


# ============ 段级生成（带质检门） ============
def _gen_guarded_v2(model, prompt, seg, gate, verbose=True):
    """
    生成单段并过质检门。
    策略：最多 GATE_MAX_ATTEMPTS 次；全不达标时取其中最好的一次（按 相似度/平坦度 综合），
    再退化为按标点拆小重生成。返回 (wav, sr, note)。
    """
    attempts = getattr(config, 'GATE_MAX_ATTEMPTS', 3)
    best = None          # (score, wav, sr, metrics)
    for k in range(attempts):
        try:
            w, sr = _gen_one(model, prompt, seg)
        except Exception as e:
            if verbose:
                print(f'      gen异常: {e}', flush=True)
            _clear_cache()
            continue
        ok, why, m = _gate_check(w, sr, gate)
        if ok:
            return w, sr, 'ok' if k == 0 else f'ok(第{k+1}次)'
        # 打分：相似度优先，其次平坦度
        score = m.get('sim', 0.0) - 5.0 * max(0.0, m.get('flatness', 0.0) - 0.05)
        if m.get('dur', 0) > getattr(config, 'BAD_DUR', 70.0):
            score -= 1.0
        if verbose:
            print(f'      第{k+1}次不合格 [{why}] sim={m.get("sim", 0):.3f} '
                  f'flat={m.get("flatness", 0):.3f}', flush=True)
        if best is None or score > best[0]:
            best = (score, w, sr, m)
        _clear_cache()

    # 拆小重试（长段更易跑偏）
    pieces = [p for p in re.split(r'(?<=[，、；])', seg) if p.strip()]
    if len(pieces) > 1 and len(seg) > 40:
        outs, ok_all = [], True
        for piece in pieces:
            pw, psr, got = None, None, False
            for _ in range(2):
                try:
                    pw, psr = _gen_one(model, prompt, piece)
                except Exception:
                    _clear_cache()
                    continue
                pok, _, _ = _gate_check(pw, psr, gate)
                if pok:
                    got = True
                    break
                _clear_cache()
                pw = None
            if not got or pw is None:
                ok_all = False
                break
            outs.append(pw)
        if ok_all and outs:
            sil = np.zeros(int(config.SAMPLE_RATE * 0.15), dtype=np.float32)
            parts = []
            for k, p in enumerate(outs):
                parts.append(p)
                if k < len(outs) - 1:
                    parts.append(sil)
            return np.concatenate(parts), config.SAMPLE_RATE, '拆段通过'

    if best is not None:
        return best[1], best[2], '保留最好一次(仍不合格)'
    return None, None, '失败'


def generate_guarded(model, prompt, seg, verbose=True):
    """兼容旧接口：不做声纹质检，仅时长+拆段防护。"""
    w, sr = None, None
    for attempt in range(3):
        try:
            w, sr = _gen_one(model, prompt, seg)
        except Exception as e:
            if verbose:
                print(f'      gen异常: {e}', flush=True)
            _clear_cache()
            continue
        if len(w) / sr <= config.BAD_DUR:
            return w, sr
        if verbose:
            print(f'      第{attempt+1}次超长({len(w)/sr:.0f}s)，重试', flush=True)
        _clear_cache()
        w = sr = None
    # 拆段
    pieces = [p for p in re.split(r'(?<=[，、；])', seg) if p.strip()]
    if len(pieces) > 1 and len(seg) > 40:
        outs, ok = [], True
        for piece in pieces:
            pw = None
            for _ in range(3):
                try:
                    pw, psr = _gen_one(model, prompt, piece)
                except Exception:
                    _clear_cache()
                    pw = None
                    continue
                if len(pw) / psr <= config.BAD_DUR:
                    break
                _clear_cache()
                pw = None
            if pw is None:
                ok = False
                break
            outs.append(pw)
        if ok and outs:
            sil = np.zeros(int(config.SAMPLE_RATE * 0.15), dtype=np.float32)
            parts = []
            for k, p in enumerate(outs):
                parts.append(p)
                if k < len(outs) - 1:
                    parts.append(sil)
            return np.concatenate(parts), config.SAMPLE_RATE
    # 兜底
    try:
        w, sr = _gen_one(model, prompt, seg)
    except Exception:
        return None, None
    if len(w) / sr > config.BAD_DUR:
        w = w[: int(config.SAMPLE_RATE * config.BAD_DUR * 0.8)]
    return w, sr


# ============ 任务状态 ============
def new_state(book_name, chapters, collection=''):
    return {
        "book": book_name,
        "collection": collection,
        "created": time.strftime('%Y-%m-%d %H:%M:%S'),
        "config": {   # 生成配置（保存后书库可完整恢复）
            "ref_audio": config.DEFAULT_REF_AUDIO,
            "ref_text": config.DEFAULT_REF_TEXT,
            "max_chars": config.MAX_CHARS_PER_SEG,
            "batch_size": config.BATCH_SIZE,
        },
        "chapters": [
            {"index": c["index"], "title": c["title"], "chars": len(c["text"].replace('\n', '')),
             "segs_total": len(split_segments(c["text"])), "done": False}
            for c in chapters
        ],
        "ref_audio": os.path.basename(config.DEFAULT_REF_AUDIO),
        "status": "running",
    }


def state_path(book_dir):
    return os.path.join(book_dir, 'task_state.json')


def chapters_path(book_dir):
    return os.path.join(book_dir, '_chapters.json')


def save_chapters(book_dir, chapters):
    """持久化章节快照，供断点续跑时恢复文本（无需重新解析源文件）。"""
    with open(chapters_path(book_dir), 'w', encoding='utf-8') as f:
        json.dump(chapters, f, ensure_ascii=False)


def load_chapters(book_dir):
    """从章节快照恢复 chapters（含 text）。"""
    p = chapters_path(book_dir)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return None


def save_state(book_dir, st):
    with open(state_path(book_dir), 'w', encoding='utf-8') as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def load_state(book_dir):
    p = state_path(book_dir)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return None


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:60] or '章'


def chunk_cache_key(ref_audio):
    """
    chunk 缓存的"指纹"：任何影响「文本 → 音频」对应关系的参数变了，旧段就不能复用。

    为什么必须要有它：段级缓存是按「段号」命名的。若分段长度从 205 字改成 105 字，
    同一段号对应的文本完全不同 —— 直接复用旧段会拿 A 的音频配 B 的文本。
    参考音色或采样参数变化同理（音色会前后不一致）。

    返回一个可读的短标识，形如 c105_reftight_a1b2c3。
    """
    import hashlib
    try:
        size = os.path.getsize(ref_audio) if ref_audio and os.path.exists(ref_audio) else 0
    except OSError:
        size = 0
    base = os.path.splitext(os.path.basename(ref_audio or ''))[0] or 'noref'
    parts = [
        f'chars={config.MAX_CHARS_PER_SEG}',
        f'segv={getattr(config, "SEG_VERSION", 1)}',   # 切分算法版本（算法改了必须变）
        f'ref={base}:{size}',
        f'temp={getattr(config, "TEMPERATURE", None)}',
        f'topp={getattr(config, "TOP_P", None)}',
        f'rep={getattr(config, "REPETITION_PENALTY", None)}',
        f'maxnew={config.MAX_NEW_TOKENS}',
    ]
    h = hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest()[:6]
    safe_ref = re.sub(r'[^0-9A-Za-z_]', '', base)[:12] or 'noref'
    return f'c{config.MAX_CHARS_PER_SEG}_{safe_ref}_{h}'


# ============ 合成一本书 ============
def synthesize_book(book_dir, chapters, ref_audio=None, ref_text=None,
                    stop_event=None, progress_cb=None, collection='', book_title=None):
    """
    逐章合成。断点续跑：已有 chunk / 已完成的章自动跳过。
    collection/book_title: 合集名与分册名（用于文件名前缀与状态记录）。
    stop_event: threading.Event，置位则安全停止（保留进度）。
    progress_cb: 可选回调 (chapter_idx, seg_done, seg_total, audio_sec)。
    返回 (book_mp3_path, state)。
    """
    os.makedirs(os.path.join(book_dir, '_chunks'), exist_ok=True)
    book_name = book_title or os.path.basename(book_dir)
    save_chapters(book_dir, chapters)  # 持久化章节快照（断点续跑用）
    st = load_state(book_dir)
    if st is None:
        st = new_state(book_name, chapters, collection=collection)
    else:
        # 恢复：更新 chapters 文本（保留已完成的 mp3 记录）
        old_ch = {c['index']: c for c in st['chapters']}
        st['chapters'] = [
            {"index": c["index"], "title": c["title"], "chars": len(c["text"].replace('\n', '')),
             "segs_total": len(split_segments(c["text"])),
             "done": old_ch.get(c["index"], {}).get('done', False),
             "mp3": old_ch.get(c["index"], {}).get('mp3')}
            for c in chapters
        ]
        if collection:
            st['collection'] = collection
    st['book'] = book_name
    # 参考音色：本次传入 → 本书保存的配置（书库"继续"）→ 默认位置。
    # 必须在写状态之前解析好——chunk 缓存能否复用取决于它；也必须在加载模型前
    # 就给出「缺音频/缺文本」的可操作提示，别让用户等半分钟再看到英文异常。
    st.setdefault('config', {})
    cfg = st.get('config', {})
    eff_ref, eff_text = resolve_reference(ref_audio or cfg.get('ref_audio'),
                                          ref_text or cfg.get('ref_text'))
    st['config']['ref_audio'] = eff_ref
    st['config']['ref_text'] = eff_text

    # ---- chunk 缓存指纹：参数变了就绝不复用旧段（防止段号错位）----
    cur_key = chunk_cache_key(eff_ref)
    old_key = cfg.get('chunk_key')
    reuse_chunks = (old_key == cur_key)
    if old_key is not None and not reuse_chunks:
        print(f'!! chunk 缓存指纹变化 {old_key} -> {cur_key}：'
              f'分段长度/参考音色/采样参数已变更，旧段不再复用，将重新生成', flush=True)
    st['config'].update(chunk_key=cur_key,
                        max_chars=config.MAX_CHARS_PER_SEG,
                        batch_size=config.BATCH_SIZE)
    st['status'] = 'running'
    save_state(book_dir, st)

    print(f'>> 生成配置: 分段≤{config.MAX_CHARS_PER_SEG}字 · 段间静音{config.SEG_SILENCE_SEC}s · '
          f'采样 temp={getattr(config, "TEMPERATURE", None)}/top_p={getattr(config, "TOP_P", None)}/'
          f'rep={getattr(config, "REPETITION_PENALTY", None)} · max_new={config.MAX_NEW_TOKENS} · '
          f'参考={os.path.basename(eff_ref)} · chunk指纹={cur_key}'
          f'{"（复用旧段）" if reuse_chunks else "（不复用旧段）"}', flush=True)

    model, prompt = get_model()
    # 无论默认音色有没有预构建成功，都用本次实际生效的参考音色重建 prompt，
    # 保证 prompt 与 eff_ref 一致（全新用户走的就是自己上传的那段）。
    prompt = set_reference(eff_ref, eff_text)
    # 质检门以实际使用的参考音色为锚点
    gate = get_gate(eff_ref)

    total_audio = 0.0
    gate_stats = {'checked': 0, 'regen': 0, 'worst_sim': 9.0}
    for ci, ch in enumerate(chapters):
        if stop_event is not None and stop_event.is_set():
            st['status'] = 'paused'
            save_state(book_dir, st)
            break
        # 断点优化：该章已完成且有 mp3，直接跳过
        if (st['chapters'][ci].get('done') and st['chapters'][ci].get('mp3')
                and os.path.exists(os.path.join(book_dir, st['chapters'][ci]['mp3']))):
            print(f'> 跳过已完成的章节 {ci+1} ({ch["title"]})', flush=True)
            if progress_cb:
                progress_cb(ch['index'], len(split_segments(ch['text'])), len(split_segments(ch['text'])), 0.0)
            continue
        segs = split_segments(ch['text'])
        ch_dir = os.path.join(book_dir, '_chunks')
        prefix = f"{safe_name(st['book'])}_ch{ch['index']:02d}"

        # 已有段（仅在 chunk 指纹一致时才复用，否则会段号错位）
        ch_wavs = {}
        existing = {}
        if reuse_chunks:
            existing = {int(os.path.basename(p).split('_seg')[1].split('.')[0]):
                        p for p in glob.glob(os.path.join(ch_dir, f'{prefix}_seg*.wav'))}
        pending = []
        for si, seg in enumerate(segs):
            if si in existing:
                try:
                    w, _ = sf.read(existing[si])
                    ch_wavs[si] = w
                    continue
                except Exception:
                    pass
            pending.append((si, seg))

        if progress_cb:
            progress_cb(ch['index'], len(ch_wavs), len(segs), 0.0)
        # 分批生成
        for b in range(0, len(pending), config.BATCH_SIZE):
            if stop_event is not None and stop_event.is_set():
                st['status'] = 'paused'
                save_state(book_dir, st)
                return None, st
            batch = pending[b:b + config.BATCH_SIZE]
            idxs = [x[0] for x in batch]
            texts = [x[1] for x in batch]
            try:
                wavs, sr = model.generate_voice_clone(
                    text=texts, language=['Chinese'] * len(texts),
                    voice_clone_prompt=prompt,
                    max_new_tokens=config.MAX_NEW_TOKENS, **_gen_kwargs())
                for off, w in enumerate(wavs):
                    i = idxs[off]
                    if w.dtype != np.float32:
                        w = w.astype(np.float32)
                    # 段级质检：时长 + 音色相似度 + 段内滑窗 + 噪声谱平坦度
                    ok, why, m = _gate_check(w, sr, gate)
                    if not ok:
                        print(f'   seg{i} 质检不合格 [{why}] → 重生成', flush=True)
                        wg, srg, note = _gen_guarded_v2(model, prompt, texts[off], gate)
                        if wg is not None:
                            w, sr = wg, srg
                            print(f'   seg{i} 重生成结果: {note}', flush=True)
                        gate_stats['regen'] += 1
                    gate_stats['checked'] += 1
                    if m.get('sim') is not None and m['sim'] < gate_stats.get('worst_sim', 9):
                        gate_stats['worst_sim'] = m['sim']
                    path = os.path.join(ch_dir, f'{prefix}_seg{i:05d}.wav')
                    sf.write(path, w, sr)
                    ch_wavs[i] = w
                    total_audio += len(w) / sr
            except Exception as e:
                print(f'   batch {idxs} 失败: {e}，单段重试', flush=True)
                _clear_cache()
                for i, seg in batch:
                    wg, srg, note = _gen_guarded_v2(model, prompt, seg, gate)
                    if wg is None:
                        continue
                    if note != 'ok':
                        print(f'   seg{i} {note}', flush=True)
                    path = os.path.join(ch_dir, f'{prefix}_seg{i:05d}.wav')
                    sf.write(path, wg, srg)
                    ch_wavs[i] = wg
                    total_audio += len(wg) / srg
                    gate_stats['regen'] += 1
            if progress_cb:
                progress_cb(ch['index'], len(ch_wavs), len(segs), total_audio)
            save_state(book_dir, st)

        # 完成该章：拼接 → mp3
        if len(ch_wavs) >= len(segs):
            order = sorted(ch_wavs.keys())
            sil = np.zeros(int(config.SAMPLE_RATE * config.SEG_SILENCE_SEC), dtype=np.float32)
            parts = []
            for k, i in enumerate(order):
                w = ch_wavs[i]
                if w.dtype != np.float32:
                    w = w.astype(np.float32)
                parts.append(w)
                if k < len(order) - 1:
                    parts.append(sil)
            full = np.concatenate(parts)
            wav_path = os.path.join(book_dir, f"{prefix}_全章.wav")
            mp3_path = os.path.join(book_dir, f"{prefix}_{safe_name(ch['title'])}.mp3")
            sf.write(wav_path, full, config.SAMPLE_RATE)
            subprocess.run([config.FFMPEG, '-y', '-i', wav_path, '-codec:a', 'libmp3lame',
                            '-qscale:a', '2', mp3_path], check=False, capture_output=True)
            os.remove(wav_path)
            st['chapters'][ci]['done'] = True
            st['chapters'][ci]['mp3'] = os.path.basename(mp3_path)
            save_state(book_dir, st)
            print(f'>> 完成 {os.path.basename(mp3_path)} ({len(full)/config.SAMPLE_RATE/60:.1f} 分钟)', flush=True)

    # 全部完成 → 合并整本
    if all(c['done'] for c in st['chapters']):
        mp3s = []
        for c in st['chapters']:
            p = os.path.join(book_dir, c['mp3'])
            if os.path.exists(p):
                mp3s.append(p)
        if mp3s:
            full_mp3 = os.path.join(book_dir, f"{safe_name(st['book'])}_全书.mp3")
            lst = os.path.join(book_dir, '_concat.txt')
            with open(lst, 'w', encoding='utf-8', newline='\n') as f:
                for m in mp3s:
                    f.write(f"file '{m}'\n")
            subprocess.run([config.FFMPEG, '-y', '-f', 'concat', '-safe', '0', '-i', lst,
                            '-c', 'copy', full_mp3], check=False, capture_output=True)
            # 播放清单
            m3u = os.path.join(book_dir, f"{safe_name(st['book'])}_播放清单.m3u")
            with open(m3u, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                for m in mp3s:
                    f.write(f'#EXTINF:-1, {os.path.basename(m)}\n')
                    f.write(f'file:///{m.replace(chr(92), "/")}\n')
            st['status'] = 'done'
            st['full_mp3'] = os.path.basename(full_mp3)
            save_state(book_dir, st)
            return full_mp3, st
    st['status'] = 'paused'
    save_state(book_dir, st)
    return None, st


def list_books():
    """列出 output/ 下所有已生成/进行中的书目录。"""
    books = []
    if not os.path.isdir(config.OUTPUT_BASE):
        return books
    for d in os.listdir(config.OUTPUT_BASE):
        bdir = os.path.join(config.OUTPUT_BASE, d)
        if os.path.isdir(bdir):
            st = load_state(bdir)
            books.append({"dir": bdir, "name": d, "state": st})
    return books