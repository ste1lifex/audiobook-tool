# -*- coding: utf-8 -*-
"""
为开源整理代码：把一次性/历史脚本移出主目录，清理运行时产物。
原则：**只移动不删除**（移错了还能拿回来），且每一步都打印清单。

目录约定（开源后）：
  scripts/            对外可用的命令行工具（有人会用得上）
  scripts/_dev/       开发期一次性诊断脚本（打补丁用，不是产品）
  scripts/_legacy/    旧管线脚本（已被 audiobook_tool 取代）
"""
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SC = os.path.join(ROOT, 'scripts')
APP = os.path.join(ROOT, 'audiobook_tool')

# ── 1) 运行时产物（每次跑都会重新生成，不该进仓库）
RUNTIME = [
    os.path.join(APP, 'ui.pid'),
    os.path.join(APP, 'uilog.txt'),
    os.path.join(APP, 'uilog_err.txt'),
    os.path.join(ROOT, '_res'),
]

# ── 2) 开发期一次性脚本（本次调查过程中写的，用于定位具体问题）
DEV = [
    'analyze_dialogue.py', 'analyze_dialogue2.py', 'analyze_resegment.py',
    'ab_ch33_transcribe.py', 'make_ab_ch33.py', 'make_consistency_check.py',
    'consistency_audit.py',            # v1，已被 v2 取代
    'diag_galaxy_epub.py', 'diag_galaxy_titles.py',
    'test_speaker.py',                 # 临时探针，正式回归是 test_speaker_rules.py
    'build_package.py',                # 打包脚本，指向本机路径
    'voice_quality_audit.py',          # 早期审计，已被 consistency_audit2 取代
]

# ── 3) 旧管线脚本（指向旧的 output/audiobook 流程，已被 audiobook_tool 取代）
LEGACY = [
    'audiobook_finalize.py', 'batch_synth.py', 'batch_synth_ch2.py',
    'batch_synth_next.py', 'synth_guard.py', 'merge_chunks.py',
    'fix_bad_segments.py', 'fix_noise_tail.py', 'verify_fix_seg45.py',
    'diag_batch.py', 'diag_seg0.py', 'timing_test.py',
    'qwen3_tts_test.py', 'qwen_long_test.py', 'qwen_clone_ref_voice.py',
    'indextts_test.py', 'indextts_speed_test.py', 'cosyvoice_synth_ch1.py',
]

# ── 4) 保留为正式工具（开源后别人会用）
KEEP = [
    'regen_chapter.py',        # 单章重做（带断点续跑 + 质检门）
    'make_chapter_mp3.py',     # chunk 目录 → 章节 mp3
    'consistency_audit2.py',   # 段间漂移审计（时长归一）
    'compare_chapter_versions.py',
    'smoke_test_engine.py',    # 改代码后的端到端冒烟测试
    'build_tight_ref.py',      # 从源音频重建参考音频
    'speaker_calib.py',        # 声纹打分器标定
    'transcribe_ref_voice.py',   # 生成参考音频的台词时间轴
    'verify_parse.py',         # 电子书解析回归测试
    'verify_seg_v2.py',        # 切分算法回归测试
    'test_ui_path.py',         # 段缓存指纹回归测试
    'test_speaker_rules.py',   # 说话人归属规则受控测试
]


def move(files, sub, root=SC):
    dst = os.path.join(root, sub)
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in files:
        s = os.path.join(root, f)
        if os.path.isfile(s):
            shutil.move(s, os.path.join(dst, f))
            n += 1
    moved = sorted(os.listdir(dst))
    return n, dst, moved


def main():
    print('=== 1) 删除运行时产物 ===')
    for p in RUNTIME:
        if os.path.isdir(p):
            n = sum(len(fs) for _, _, fs in os.walk(p))
            shutil.rmtree(p)
            print(f'  已删目录 {os.path.relpath(p, ROOT)}  ({n} 个文件)')
        elif os.path.isfile(p):
            os.remove(p)
            print(f'  已删文件 {os.path.relpath(p, ROOT)}')

    print('\n=== 2) 开发脚本 → scripts/_dev/ ===')
    n, d, names = move(DEV, '_dev')
    print(f'  移入 {n} 个: {", ".join(names)}')

    print('\n=== 3) 旧管线脚本 → scripts/_legacy/ ===')
    n, d, names = move(LEGACY, '_legacy')
    print(f'  移入 {n} 个')

    print('\n=== 4) scripts/ 主目录剩下的（对外工具）===')
    left = sorted(f for f in os.listdir(SC)
                  if os.path.isfile(os.path.join(SC, f)))
    for f in left:
        tag = 'OK ' if f in KEEP else '?? 未归类'
        print(f'  [{tag}] {f}')
    extra = [f for f in left if f not in KEEP]
    if extra:
        print(f'\n  ⚠ 有 {len(extra)} 个未归类: {extra}')
    else:
        print(f'\n  全部 {len(left)} 个已归类 ✓')

    print('\n=== 5) 未归类文件检查结束 ===')


if __name__ == '__main__':
    main()
