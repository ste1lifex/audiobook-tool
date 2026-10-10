# -*- coding: utf-8 -*-
"""
speaker 归属规则的受控单元测试 —— 每个用例的正确答案是已知的。
（之前用真实正文"眼看上去对不对"验证，是不可靠的：无法确认 ground truth）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import speaker  # noqa: E402

ROSTER = ['索尔', '领事', '杜雷', '布劳恩', '悦石', '亨特']

CASES = [
    # (说明, 文本, 期望的 对话->角色 映射列表)
    ('前置归属  索尔说：「…」',
     '索尔说：「我同意。」',
     ['索尔']),
    ('后置归属  「…」索尔说。',
     '「我同意。」索尔说。',
     ['索尔']),
    ('后置归属（段落式）',
     '「我同意。」索尔说。他把水递过来。',
     ['索尔']),
    ('A说B说的话 A/B 交替（后置，各归各的）',
     '「第一句。」索尔说。「第二句。」领事说。「第三句。」杜雷说。',
     ['索尔', '领事', '杜雷']),
    ('前置+后置混用',
     '领事说：「第一句。」索尔说：「第二句。」',
     ['领事', '索尔']),
    ('后置归属后面还跟着旁白',
     '「第一句。」索尔说。他站起身走到窗边。',
     ['索尔']),
    ('无归属词 → 沿用上一个被点名的角色',
     '「第一句。」索尔说。「第二句。」「第三句。」',
     ['索尔', '索尔', '索尔']),
    ('全程无归属词 → 通用对话音色',
     '「第一句。」「第二句。」',
     ['对话', '对话']),
    ('引号后是另一个角色的动作（不构成归属）',
     '「第一句。」索尔说。「第二句。」领事靠向石头。「第三句。」',
     ['索尔', '索尔', '索尔']),
    ('不在名册里的名字 → 不算归属',
     '「第一句。」陌生人说。',
     ['对话']),
]


def main():
    ok = bad = 0
    for desc, text, want in CASES:
        turns = speaker.split_turns(text)
        voices = speaker.assign_voices(turns, ROSTER)
        got = [v for (k, _), v in zip(turns, voices) if k == '对话']
        flag = 'OK  ' if got == want else 'FAIL'
        if got == want:
            ok += 1
        else:
            bad += 1
        print(f'[{flag}] {desc}')
        print(f'         文本: {text}')
        print(f'         轮次: {[(k, t[:12]) for k, t in turns]}')
        print(f'         期望: {want}')
        print(f'         实得: {got}')
        print()
    print(f'=== {ok} 通过 / {bad} 失败 ===')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
