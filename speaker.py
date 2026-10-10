# -*- coding: utf-8 -*-
"""
多角色支持 —— 第 1 层：把书变成"带说话人标注的剧本"。

为什么单独一层：
  多角色的硬前提是「一段只能有一个说话人」。
  实测《海伯利安的隕落》有 42% 的段同时含旁白与对话，这类段无法分配单一音色。
  所以必须先按说话人轮次切分，再按音色合并。

本模块不碰引擎、不需要 GPU，纯文本处理，可离线验证。
输出形如 [{'voice': '旁白'|'悦石'|'对话', 'text': '...'}]。

归属策略（规则优先，不依赖 LLM）：
  实测规则可覆盖 72~80% 的对话段，剩下的落到通用"对话"音色。
  连 Alexandria 都要跑两遍 LLM 去修归属错误 —— 说明 LLM 归属并非现成好用，
  所以这里先用确定性规则，需要更高准确率时再接 LLM（接口已留好）。
"""
import re
from collections import Counter

# 说话动词（按长度优先匹配）
# 注意：裸「道」要加否定后顾，否则"我不知道"会被切成"我不知"+"道"
SAY_VERBS = (
    '说道', '问道', '答道', '喊道', '叫道', '低声说', '接着说', '继续说', '补充道',
    '笑着说', '说', '问', '答', '回答', '回应', '低语', '喃喃', '嘟囔', '咕哝',
    '嘀咕', '插嘴', '打断', '重复', '强调', '提醒', '警告', '宣布', '承认', '否认',
    '指出', '解释', '询问', '告诉', '感叹', '吼道',
)
_VERB_ALT = ('(?:' + '|'.join(sorted(SAY_VERBS, key=len, reverse=True))
             + '|(?<![知味难该能通街大 moral])道)')

# 明显不是人名的词（代词、连词、常见动宾碎片）
STOP_WORDS = frozenset("""
我 你 他 她 它 我们 你们 他们 她们 咱们 大家 有人 两人 双方 对方 那人 此人 对方
然后 于是 接着 后来 这时 此时 现在 当时 之后 之前 终于 突然 忽然 立刻 马上 已经
仍然 还是 只是 但是 可是 不过 因此 所以 因为 如果 虽然 然而 同时 另外 此外 其实
显然 当然 知道 不知 我知 你知 他知 我是 你是 他是 不要 没有 什么 怎么 这样 那样
一个 这个 那个 这些 那些 一样 时候 地方 东西 事情 问题 可能 应该 必须 需要
继续 比如 例如 意思是 还没有 不可 不能 不会 就是 不是 有人 自己
""".split())

# 引号对（左右）
_OPEN_TO_CLOSE = {'「': '」', '『': '』', '“': '”', '‘': '’', '"': '"'}
_CLOSERS = set(_OPEN_TO_CLOSE.values())

VOICE_NARRATOR = '旁白'
VOICE_DIALOGUE = '对话'      # 归属不出来的对话统一走这个音色


def split_turns(text):
    """
    把正文切成说话人轮次 [(kind, text)]，kind ∈ {'旁白','对话'}。
    用栈处理嵌套引号（引号内的引号算同一轮对话）。
    """
    text = re.sub(r'\s+', '', text)
    out, buf, stack = [], '', []
    for ch in text:
        if ch in _OPEN_TO_CLOSE:
            if not stack and buf:
                out.append(('旁白', buf))
                buf = ''
            stack.append(_OPEN_TO_CLOSE[ch])
            buf += ch
        elif ch in _CLOSERS and stack and stack[-1] == ch:
            stack.pop()
            buf += ch
            if not stack:
                out.append(('对话', buf))
                buf = ''
        else:
            buf += ch
    if buf:
        out.append(('对话' if stack else '旁白', buf))
    return [(k, t) for k, t in out if t.strip()]


def build_roster(text, min_count=3, top=8):
    """
    从全书自动提取主要角色名：找「名字 + 说话动词」的模式，过滤非人名词。
    返回按出现次数排序的名单（不含旁白/对话）。
    """
    cand = Counter()
    for m in re.finditer(r'([\u4e00-\u9fff]{2,4})(?:' + _VERB_ALT + r')', text):
        w = m.group(1)
        if w in STOP_WORDS or len(w) < 2:
            continue
        # 去掉"XX继续/XX又"这类动词性后缀残留
        if any(w.endswith(v[-1]) for v in ('道', '说', '问', '答')) and w not in STOP_WORDS:
            pass
        cand[w] += 1
    # 再补一遍：名字可能紧跟在引号后（「…」XX说）
    for m in re.finditer(r'[」”』"]\s*([\u4e00-\u9fff]{2,4})(?:' + _VERB_ALT + r')', text):
        w = m.group(1)
        if w not in STOP_WORDS:
            cand[w] += 1
    return [w for w, c in cand.most_common(top * 3) if c >= min_count][:top]


def _window(turns, i, before=1, after=1):
    """取轮次 i 前后的窗口文本（用于找归属）。"""
    lo = max(0, i - before)
    hi = min(len(turns), i + after + 1)
    return ''.join(t for _, t in turns[lo:hi])


def _find_name(narr, roster_set, from_end=False):
    """
    在一段旁白里找"说话人"。
    from_end=True 用于**前置**归属（`索尔说：「…」`）——取最靠近引号的最后一个名字；
    from_end=False 用于**后置**归属（`「…」索尔说。`）——取最靠近引号的第一个名字。
    """
    hits = [(m.start(), m.group(1)) for m in re.finditer(r'([\u4e00-\u9fff]{2,4})' + _VERB_ALT, narr)
            if m.group(1) in roster_set]
    if not hits:
        hits = [(m.start(), m.group(1)) for m in re.finditer(r'([\u4e00-\u9fff]{2,4})[：:]', narr)
                if m.group(1) in roster_set]
    if not hits:
        return None
    return (hits[-1] if from_end else hits[0])[1]


# 以这些结尾 = 引出**后面**的引号（前置归属）；否则 = 归属**前面**的引号（后置归属）
_PRE_TAIL = ('：', ':')


def _is_pre_attribution(narr):
    """`索尔说：` 是前置（引出下一句）；`索尔说。`/`索尔说，` 是后置（归属上一句）。"""
    return narr.rstrip().endswith(_PRE_TAIL)


def assign_voices(turns, roster):
    """
    给每个轮次分配音色 key。

    ⚠ 两个容易错的关键点（都靠受控用例测出来的）：

    1) **后置归属优先**。中文最常见是 `「…」索尔说。` —— 归属词跟在引号后面。
       早期实现取"窗口里最后一个命中"，把后置归属错配给了下一句。

    2) **用结尾标点区分前置/后置**：
         `索尔说：` 以冒号结尾 → 引出**下一句**（不能拿来归属本句）
         `索尔说。`/`索尔说，` → 归属**上一句**
       没有这一条时 `领事说：「A」索尔说：「B」` 会把 A 也判给索尔。

    优先级：后置(本句) → 前置(本句) → 沿用上一个被点名的角色 → 通用「对话」音色
    """
    roster_set = set(roster)
    out, last_named = [], None
    for i, (kind, t) in enumerate(turns):
        if kind == '旁白':
            out.append(VOICE_NARRATOR)
            continue
        name = None
        # ① 后置：紧随其后的旁白，且它不以冒号结尾（否则那是下一句的前置归属）
        if i + 1 < len(turns) and turns[i + 1][0] == '旁白':
            nxt = turns[i + 1][1]
            if not _is_pre_attribution(nxt):
                name = _find_name(nxt, roster_set, from_end=False)
        # ② 前置：紧邻其前的旁白，且它以冒号结尾（`XX说：「…」`）
        if not name and i - 1 >= 0 and turns[i - 1][0] == '旁白':
            prv = turns[i - 1][1]
            if _is_pre_attribution(prv):
                name = _find_name(prv, roster_set, from_end=True)
        if name:
            last_named = name
            out.append(name)
        else:
            out.append(last_named or VOICE_DIALOGUE)
    return out


def merge_by_voice(turns, voices, mx):
    """
    按音色合并相邻轮次，再在每个"音色连续段"内按长度切到 <= mx。
    段数由**音色切换次数**决定，而不是轮次数量 —— 这是控制段数膨胀的关键。
    """
    # 1) 合并相邻同音色
    groups = []
    for (kind, t), v in zip(turns, voices):
        if groups and groups[-1][0] == v:
            groups[-1][1] += t
        else:
            groups.append([v, t])
    # 2) 组内按长度切（复用 engine 的切分，保证规则一致）
    import engine
    out = []
    for v, t in groups:
        # 旁白组可以直接用长度切分器；对话组同理（都保证不跨音色）
        for piece in engine.split_segments(t, mx):
            out.append({'voice': v, 'text': piece})
    return out


def segment_for_voices(text, mx=105, roster=None, top=8):
    """
    主入口：返回 [{'voice','text'}]。
    roster=None 时自动从本书提取。
    """
    turns = split_turns(text)
    if roster is None:
        roster = build_roster(text, top=top)
    voices = assign_voices(turns, roster)
    segs = merge_by_voice(turns, voices, mx)
    return segs, roster


def stats(segs, roster):
    """给一份剧本统计（用于人工核对质量）。"""
    by_voice = Counter(s['voice'] for s in segs)
    chars = Counter()
    for s in segs:
        chars[s['voice']] += len(s['text'])
    n = len(segs)
    named = sum(c for v, c in by_voice.items()
                if v not in (VOICE_NARRATOR, VOICE_DIALOGUE))
    return dict(n=n, by_voice=by_voice, chars=chars,
                named_segs=named, named_ratio=named / max(1, n),
                roster=roster)
