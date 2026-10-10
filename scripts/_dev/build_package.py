# -*- coding: utf-8 -*-
"""
构建可移植发行包 _pack\audiobook_portable

包含: 程序代码 + 工具脚本 + 评测框架文档 + qwen_tts 源码 + campplus 模型 + ffmpeg
不包含: 用户的电子书、生成好的音频、Qwen3-TTS 模型权重(2.3GB, 由 setup.ps1 下载)

同时把脚本里硬编码的 ... 路径改写成"向上找包根"的可移植写法。
"""
import os
import re
import shutil
import sys

SRC = r'.'
PROJ = os.path.join(SRC, 'audiobook_tool')       # 项目根（代码/脚本/文档/评测/模型都在这里）
PKG = os.path.join(SRC, '_pack', 'audiobook_portable')
MODEL_SRC = os.path.join(PROJ, 'models', 'Qwen3-TTS-12Hz-0.6B-Base')
WITH_MODEL = '--with-model' in sys.argv          # 是否把模型权重(2.34GB)打进包

# ---------- 要打包的脚本（精选：在用的工具 + 评测框架）----------
TOOLS = ['regen_chapter.py', 'make_chapter_mp3.py', 'voice_quality_audit.py',
         'compare_chapter_versions.py', 'smoke_test_engine.py', 'build_tight_ref.py',
         'speaker_calib.py', 'test_ui_path.py', 'verify_parse.py', 'transcribe_ref_voice.py']

EVAL_SCRIPTS = ['make_segments.py', 'gen_qwen3.py', 'transcribe.py', 'transcribe_dir.py',
                'score.py', 'score_chapter.py', 'persegment_and_listen.py',
                'buzz_diagnose.py', 'buzz_specificity.py', 'cer_calib.py',
                'spectrum_check.py', 'download_models.py']

EVAL_REPORTS = ['baseline.md']

APP_FILES = ['app.py', 'config.py', 'ebook.py', 'engine.py', 'quality.py', 'text_clean.py',
             'start_ui.ps1', 'stop_ui.ps1', 'start_ui.bat', 'stop_ui.bat']

# ---------- 路径改写：把开发机绝对路径换成可移植写法 ----------
_BOOT = '''
# --- 可移植路径引导（由打包脚本注入）---
def _find_pkg_root(_start):
    _d = _start
    for _ in range(6):
        if os.path.isdir(os.path.join(_d, 'audiobook_tool')):
            return _d
        _d = os.path.dirname(_d)
    return _start


_PKG_ROOT = _find_pkg_root(os.path.dirname(os.path.abspath(__file__)))
'''


def rewrite(text, depth_note):
    """可移植性改写。

    历史上这里会把脚本里硬编码的开发机绝对路径替换成"向上找包根"的
    `_PKG_ROOT` 表达式。这些硬编码路径已经从仓库里清理干净，
    所以现在不再做路径替换；唯一保留的改写是 docstring 的反斜杠 raw-string
    修正，见 `copy_tree_file()`。
    """
    return text.lstrip('\ufeff').replace('\r\n', '\n'), 0


def copy_tree_file(src, dst, rewrite_paths=True):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not rewrite_paths or not src.endswith('.py'):
        shutil.copy2(src, dst)
        return 0
    with open(src, encoding='utf-8') as f:
        t = f.read()
    t, n = rewrite(t, '')
    # 模块文档字符串里若含反斜杠路径，改成 raw 字符串，消除 SyntaxWarning
    # （注意文件首行可能是 `# -*- coding: utf-8 -*-`，所以不能直接用 startswith）
    m = re.search(r'("""|\'\'\')(.*?)\1', t, re.S)
    if m and '\\' in m.group(2):
        pre = t[:m.start(1)]
        if not pre.endswith(('r', 'R', 'f', 'F')):
            t = pre + 'r' + t[m.start(1):]
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(t)
    return n


def main():
    if os.path.isdir(PKG):
        shutil.rmtree(PKG)
    os.makedirs(PKG)
    total_rewrites = 0

    # 1) 主程序（config/quality 已是可移植写法，app.py 里只是文档字符串提到路径，都不改写）
    NO_REWRITE = {'config.py', 'quality.py', 'app.py'}
    for f in APP_FILES:
        s = os.path.join(PROJ, f)
        if os.path.isfile(s):
            total_rewrites += copy_tree_file(
                s, os.path.join(PKG, 'audiobook_tool', f),
                rewrite_paths=(f not in NO_REWRITE))
    print(f'[1/7] audiobook_tool: {len(os.listdir(os.path.join(PKG, "audiobook_tool")))} 个文件')

    # 2) 工具脚本
    os.makedirs(os.path.join(PKG, 'scripts'), exist_ok=True)
    for f in TOOLS:
        s = os.path.join(PROJ, 'scripts', f)
        if os.path.isfile(s):
            total_rewrites += copy_tree_file(s, os.path.join(PKG, 'scripts', f))
    print(f'[2/7] scripts: {len(os.listdir(os.path.join(PKG, "scripts")))} 个')

    # 3) 评测框架
    td = os.path.join(PKG, 'tts_eval')
    os.makedirs(os.path.join(td, 'scripts'), exist_ok=True)
    os.makedirs(os.path.join(td, 'reports'), exist_ok=True)
    for f in EVAL_SCRIPTS:
        s = os.path.join(PROJ, 'tts_eval', 'scripts', f)
        if os.path.isfile(s):
            total_rewrites += copy_tree_file(s, os.path.join(td, 'scripts', f))
    for f in EVAL_REPORTS:
        s = os.path.join(PROJ, 'tts_eval', 'reports', f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(td, 'reports', f))
    s = os.path.join(PROJ, 'tts_eval', 'README.md')
    if os.path.isfile(s):
        shutil.copy2(s, os.path.join(td, 'README.md'))
    print(f'[3/7] tts_eval: {len(os.listdir(os.path.join(td, "scripts")))} 脚本 + 文档')

    # 4) vendor: qwen_tts 源码（保证与开发机同版本行为）
    vd = os.path.join(PKG, 'vendor', 'Qwen3-TTS')
    os.makedirs(vd, exist_ok=True)
    shutil.copytree(os.path.join(PROJ, 'vendor', 'Qwen3-TTS', 'qwen_tts'), os.path.join(vd, 'qwen_tts'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'), dirs_exist_ok=True)
    for f in ('pyproject.toml', 'MANIFEST.in', 'LICENSE', 'README.md'):
        s = os.path.join(PROJ, 'vendor', 'Qwen3-TTS', f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(vd, f))
    print('[4/7] vendor/Qwen3-TTS: qwen_tts 源码 + 打包配置')

    # 5) 模型与工具
    os.makedirs(os.path.join(PKG, 'models'), exist_ok=True)
    shutil.copy2(os.path.join(PROJ, 'models', 'campplus.onnx'),
                 os.path.join(PKG, 'models', 'campplus.onnx'))
    os.makedirs(os.path.join(PKG, 'bin'), exist_ok=True)
    for exe in ('ffmpeg.exe', 'ffprobe.exe'):
        s = os.path.join(PROJ, 'bin', exe)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(PKG, 'bin', exe))
    print('[5/7] models/campplus.onnx + bin/ffmpeg,ffprobe')

    # 6) 参考音频（只带程序默认要用的，不带用户的语音包素材）
    os.makedirs(os.path.join(PKG, 'reference_audio'), exist_ok=True)
    for f in ('ref_tight.wav', 'ref_clip_full.wav', 'ref_prompt.txt',
              'ref_prompt_53s.txt', 'ref_prompt_full.txt', 'ref_clip_30s.txt', 'ref_clip_53s.txt'):
        s = os.path.join(PROJ, 'reference_audio', f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(PKG, 'reference_audio', f))
    print('[6/7] reference_audio: 默认参考音频 + 文本')

    # 7) 空目录占位
    for d in ('audiobook_tool/input', 'audiobook_tool/output'):
        os.makedirs(os.path.join(PKG, d), exist_ok=True)
        with open(os.path.join(PKG, d, 'PUT_BOOKS_HERE.txt'), 'w', encoding='utf-8') as f:
            f.write('把你的 .epub/.mobi/.azw3/.txt 电子书放进这个目录，UI 会自动识别。\n'
                    if d.endswith('input') else
                    '生成结果（章节 mp3 / 全书 mp3 / 播放清单 / 段级缓存）会写到这里。\n')
    print('[7/7] input/output 占位')

    # 8) 文档与安装脚本（源在 _pack\docs，重复构建不会丢）
    #    注意：AGENT_HANDOVER.md 的唯一来源是仓库 docs\，这里不放副本（避免漂移）
    DOCS = os.path.join(SRC, '_pack', 'docs')
    root_docs = ['README.md', 'requirements.txt', 'setup.ps1',
                 'selfcheck.py', 'selftest_model.py']
    for f in root_docs:
        s = os.path.join(DOCS, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(PKG, f))
    s = os.path.join(PROJ, 'docs', 'AGENT_HANDOVER.md')
    if os.path.isfile(s):
        shutil.copy2(s, os.path.join(PKG, 'AGENT_HANDOVER.md'))
    # 可移植版的启停脚本覆盖 audiobook_tool 里的原版
    for f in ('start_ui.ps1', 'stop_ui.ps1'):
        s = os.path.join(DOCS, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(PKG, 'audiobook_tool', f))
    print('[8/9] 文档 + 可移植启停脚本')

    # 9) 模型权重（可选；2.34GB）
    if WITH_MODEL:
        md = os.path.join(PKG, 'models', 'Qwen3-TTS-12Hz-0.6B-Base')
        shutil.copytree(MODEL_SRC, md, ignore=shutil.ignore_patterns('.git*'))
        n = len([1 for _, _, fs in os.walk(md) for f in fs])
        print(f'[9/9] 模型权重已打入 models/Qwen3-TTS-12Hz-0.6B-Base（{n} 个文件）')
    else:
        print('[9/9] 跳过模型权重（未加 --with-model）')

    # 统计
    tot = sum(os.path.getsize(os.path.join(r, f))
              for r, _, fs in os.walk(PKG) for f in fs)
    print(f'\n包目录: {PKG}')
    print(f'大小:   {tot/1024/1024:.1f} MB')
    print(f'路径改写: {total_rewrites} 处')
    # 残留硬编码检查：任何盘符绝对路径（如 C:\ / F:\）都不该再出现
    ABS = re.compile(r'[A-Za-z]:[\\/]')
    left = []
    for r, _, fs in os.walk(PKG):
        for f in fs:
            if f.endswith('.py'):
                p = os.path.join(r, f)
                t = open(p, encoding='utf-8').read()
                if ABS.search(t):
                    left.append(os.path.relpath(p, PKG))
    print(f'仍含盘符绝对路径的脚本: {left if left else "无 ✅"}')


if __name__ == '__main__':
    main()
