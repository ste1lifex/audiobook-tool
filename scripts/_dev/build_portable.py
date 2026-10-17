# -*- coding: utf-8 -*-
r"""
audiobook_tool —— 自包含 Windows 发行包构建脚本（可重复执行 / 幂等）

做出来的包长这样（解压即用，不需要装 Python、不需要配环境变量、不需要管理员权限）：

    audiobook-tool-portable\
      启动.bat / start.bat          双击即用（内部用 %~dp0 相对调用包内解释器）
      自检.bat / selfcheck.bat      selfcheck.py
      停止.bat / stop.bat           关掉 7860 端口的服务
      下载模型.bat                  仅 lite 版需要（走包内 modelscope 下载权重）
      使用说明.md                    发行说明（体积、首次使用、已知限制、验证情况）
      python\                       内置 CPython 3.12（底座复制 + 依赖平铺，无 venv）
      audiobook_tool\               产品代码（server.py / static / scripts / input / output）
      models\                       full: Qwen3-TTS 权重 + campplus.onnx；lite: 仅 campplus.onnx
      bin\                          ffmpeg.exe / ffprobe.exe
      reference_audio\              空目录（参考音频有版权，不随包分发）
      tools\download_model.py       lite 的权重下载器
      docs\                         （不随包：开发期文档含书稿片段/书名/个人素材名，要带得加 --with-dev-docs）

为什么这样摆：`config.py` 的路径解析第 ③ 级就是「发行包内约定位置」
（`<包根>\models` / `bin` / `reference_audio`），而 `PROJECT_ROOT` 是 `config.py` 所在目录，
所以代码进 `audiobook_tool\`、大件放包根，正好命中 —— **零代码改动**。

────────────────────────────────────────────────────────────────────────
自包含打包的通用坑（本机实测出来的，换项目也适用，下次做打包先看这段）
────────────────────────────────────────────────────────────────────────
1) editable 安装会把开发机绝对路径烧进 site-packages
   症状：用户机上 `import 该包` 直接失败；包内残留 `F:\...`。
   检查：`ls site-packages | grep -i editable`；`grep -rl "<开发机盘符>" site-packages`。
   处理：删 `__editable__*`（`.pth` 与 `*_finder.py`）和 `*.dist-info/direct_url.json`，
        把源码目录当**实体包**拷进 site-packages（本仓库：`vendor/Qwen3-TTS/qwen_tts`）。
2) 别想靠"搬 venv"自包含（`pyvenv.cfg` 里 home/executable 是绝对路径）
   症状：复制/移动后解释器找不到 stdlib，或 pth 与 console script 指向旧位置。
   处理：**不用 venv**——复制底座解释器（去掉 Doc/include/libs/Scripts），把 venv 的
        `Lib\site-packages` 平铺进它的 `Lib\site-packages`；启动器用 `%~dp0` 相对调用。
   自证：把内置解释器放到**含中文与空格**的路径，跑 `sys.prefix` / `pip --version`。
3) "首次 import 要写盘"的依赖，遇到不可写目录会**卡死而不是报错**
   症状：只读介质 / Program Files / 受限沙箱下，import 挂几分钟且无任何输出。
   本例：`librosa → numba` 要往自己 `site-packages\...\__pycache__` 写编译缓存
        （`numba/core/caching.py: ensure_cache_path → tempfile`）。
   处理：发行说明写明「解压到**用户可写**目录」；调试时可 `NUMBA_CACHE_DIR` 指到可写处绕过。
   （附带结论：site-packages 里的 `.pyc` 虽残留编译期源路径，但 CPython 加载时用
     `_imp._fix_co_filename` 修正为实际路径——实测 `inspect.getfile` 与 numba 缓存路径
     都指向包内，故 `.pyc` 可放心随包。）
附两条同批踩到的（同属"换机才炸"）：
   a. 打包产物里别用"靠 PATH 找"的外部命令（如 `powershell`）：用 `%SystemRoot%\...` 绝对路径。
      实测：PATH 只剩 system32 时 `停止.bat` 报 `'powershell' is not recognized`。
   b. zip 里要带**顶层目录**（本例 `audiobook-tool-portable/`），否则解压会把文件撒进当前目录；
      分片 zip 各片用同一顶层名，用户才能"都解压到同一目录"合并。

用法（在仓库根或任意目录执行均可）：
    python scripts\_dev\build_portable.py --lite            # 只构建 lite 树（不打包）
    python scripts\_dev\build_portable.py --full --zip      # full 树 + 打 zip
    python scripts\_dev\build_portable.py --both --zip --split   # 两版都构建并分片打包

依赖：本机已有的开发 venv（含 torch 等全部依赖）+ 一个 Python 3.12 底座解释器。
默认路径按以下顺序自动推断，也可用命令行参数或环境变量覆盖：
    --venv         AUDIOBOOK_BUILD_VENV    （默认 <仓库>\..\envs\qwen3tts 等候选）
    --base-python  AUDIOBOOK_BUILD_BASE_PYTHON （默认读 venv 的 pyvenv.cfg home）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

# ─────────────────────────── 常量与清单 ───────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))                 # scripts\_dev\ -> 仓库根

DEFAULT_STAGE = r'F:\commuting\_pack\portable_build'
DEFAULT_OUT = os.path.join(REPO, 'dist')

# 产品代码（原样复制，不改一个字节）
APP_FILES = ['server.py', 'config.py', 'engine.py', 'quality.py',
             'ebook.py', 'text_clean.py', 'speaker.py']
APP_DIRS = ['static']
# 仓库根下的自检/安装脚本（发行包根目录）
ROOT_FILES = ['selfcheck.py', 'selftest_model.py', 'requirements.txt',
              'LICENSE', 'THIRD_PARTY_NOTICES.md']
# 文档
DOC_FILES = ['docs/AGENT_HANDOVER.md', 'docs/DESIGN.md']
DOC_DIRS = ['docs/screenshots']
# 评测文档（只带文档，不带 audio/logs/tmp 与含版权原文的 json）
EVAL_DOC = ['tts_eval/README.md', 'tts_eval/reports/baseline.md',
            'tts_eval/reports/persegment.md']
# 工具脚本目录（整目录复制；工具用「脚本的上上级目录找 config」的相对逻辑，必须保持同构）
TOOL_DIRS = ['scripts']

# VC++ 运行库（可再发行组件；干净系统缺 msvcp140 会导致 import torch 失败）
VC_DLLS = ['msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
           'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll',
           'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll',
           'concrt140.dll']
SYSTEM32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')

MODEL_DIRNAME = 'Qwen3-TTS-12Hz-0.6B-Base'
MODEL_REL = os.path.join('models', MODEL_DIRNAME)
CAMPP_REF = os.path.join('models', 'campplus.onnx')

# 开发机路径指纹：构建产物里出现任何一个都算失败
DEV_PATH_PATTERNS = [
    re.compile(r'[A-Za-z]:[\\/]commuting[\\/]', re.I),
    re.compile(r'[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}xiaoh[\\/]', re.I),
    re.compile(r'envs[\\/]qwen3tts', re.I),
]
TEXT_EXT = {'.py', '.pth', '.json', '.bat', '.cmd', '.ps1', '.cfg', '.toml',
            '.txt', '.md', '.ini', '.yaml', '.yml'}
SCAN_LIMIT = 8 * 1024 * 1024      # 单文件扫描上限

# 文档副本里的开发环境痕迹 → 发行包用中性写法（只改包里的副本，不动仓库原文）
DOC_SANITIZE = [(re.compile(r'envs[\\/]qwen3tts', re.I), '<你的 venv>')]

T0 = time.time()


def log(msg=''):
    print(f'[{time.time() - T0:7.1f}s] {msg}', flush=True)


def die(msg):
    print(f'!! {msg}', file=sys.stderr, flush=True)
    sys.exit(1)


# ─────────────────────────── 基础工具 ───────────────────────────

def robocopy(src, dst, xd=(), xf=(), mt=16):
    """robocopy 复制（Windows 原生，抗中文/空格路径，多线程）。返回复制的文件数。"""
    os.makedirs(dst, exist_ok=True)
    cmd = ['robocopy', src, dst, '/E', f'/MT:{mt}', '/NFL', '/NDL', '/NJH', '/NJS',
           '/NP', '/R:1', '/W:1', '/COPY:DAT']
    if xd:
        cmd += ['/XD'] + list(xd)
    if xf:
        cmd += ['/XF'] + list(xf)
    p = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    if p.returncode >= 8:
        die(f'robocopy 失败 rc={p.returncode}\n  {" ".join(cmd)}\n{p.stdout}\n{p.stderr}')
    # 汇总行: Files :  Total  Copied  Skipped  Mismatch  FAILED  Extras
    m = re.search(r'Files\s*:\s+(\d+)\s+(\d+)', p.stdout or '')
    return int(m.group(2)) if m else 0


def copy_file(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def copy_doc(repo, rel, tree, dst_rel):
    """复制文档到发行包，并做路径净化。返回净化处数（仓库原文不动）。"""
    src = os.path.join(repo, rel)
    if not os.path.isfile(src):
        return 0
    with open(src, encoding='utf-8', errors='replace') as f:
        t = f.read()
    n = 0
    for pat, rep in DOC_SANITIZE:
        t, k = pat.subn(rep, t)
        n += k
    dst = os.path.join(tree, dst_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write(t.lstrip('\ufeff'))
    return n


def rmtree_safe(path, must_contain):
    """删除目录前先确认目标确实是我们要删的那个（避免手滑）。"""
    ap = os.path.abspath(path)
    if not os.path.isdir(ap):
        return
    if must_contain.lower() not in ap.lower():
        die(f'拒绝删除：路径里没有 "{must_contain}" → {ap}')
    if len(ap.split(os.sep)) < 4:
        die(f'拒绝删除：路径太浅 → {ap}')
    log(f'  清理 {ap}')
    shutil.rmtree(ap)


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def count_files(path):
    return sum(len(fs) for _, _, fs in os.walk(path))


def human(n):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or u == 'TB':
            return f'{n:.1f} {u}' if u != 'B' else f'{int(n)} B'
        n /= 1024


# ─────────────────────── 参数与路径推断 ───────────────────────

def find_venv(explicit):
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get('AUDIOBOOK_BUILD_VENV')
    if env:
        return os.path.abspath(env)
    parent = os.path.dirname(REPO)
    cands = [os.path.join(parent, 'envs', 'qwen3tts'),
             os.path.join(parent, 'envs', 'audiobook'),
             os.path.join(REPO, 'envs', 'audiobook'),
             os.path.join(REPO, '.venv')]
    for c in cands:
        if os.path.isfile(os.path.join(c, 'Scripts', 'python.exe')):
            return os.path.abspath(c)
    die('找不到开发 venv，请用 --venv 指定（应指向含 Scripts\\python.exe 的目录）')


def find_base_python(venv, explicit):
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get('AUDIOBOOK_BUILD_BASE_PYTHON')
    if env:
        return os.path.abspath(env)
    cfg = os.path.join(venv, 'pyvenv.cfg')
    if os.path.isfile(cfg):
        with open(cfg, encoding='utf-8-sig') as f:
            for line in f:
                if line.lower().startswith('home'):
                    p = line.split('=', 1)[1].strip()
                    if os.path.isfile(os.path.join(p, 'python.exe')):
                        return os.path.abspath(p)
    die('找不到底座解释器，请用 --base-python 指定（应指向含 python.exe 的 Python 3.12 安装目录）')


# ─────────────────────── 使用说明 / 启动器 ───────────────────────

README_TMPL = """# 有声书生成工具 —— 便携版 · {edition}

把电子书批量转成**克隆指定音色**的有声书，全本地运行，不上传任何内容。

> 本包**已内置 Python 解释器与全部依赖**：目标电脑不需要安装 Python、不需要 pip、
> 不需要配环境变量、不需要管理员权限。但**必须有 NVIDIA 显卡与驱动**（显存建议 ≥ 6 GB）。

---

## 一、快速开始

0. **先看解压**：压缩包内有一个顶层目录 `audiobook-tool-portable\\`。
   - lite 版有两个分片（`part1of2` / `part2of2`），**两个都要解压到同一个目录**才能用；
     只解其中一个会缺文件。
1. 把压缩包解压到**任意可写的目录**（例如 `D:\\audiobook-tool`）
   - ⚠️ **不要放在 `C:\\Program Files` 这类需要管理员权限的位置**：
     首次运行时要往包内写缓存与输出，目录不可写会导致启动卡住。
   - 支持带空格、带中文的路径。
2. {model_step}
3. 双击 **`启动.bat`**（或 `start.bat`）→ 浏览器会自动打开 <http://127.0.0.1:7860>
   - 黑窗口就是服务本体，**关掉窗口 = 关闭程序**；想优雅关闭可双击 `停止.bat`。
   - 若提示端口被占用（`server exited with code 3`）：先双击 `停止.bat`，
     或换端口启动：`启动.bat 7861`。
4. 第一次使用必须**上传你自己的参考音频**：
   - 界面上选一段 **5~10 秒、单人、干净、已剪掉静音**的语音；
   - 并在「参考音色对应文本」里填**与音频逐字一致**的文字（文字错了音色会飘）。
   - 本包**不附带任何参考音频**（见第六节版权说明）。
   - 想固定默认音色：把音频放到 `reference_audio\\ref_tight.wav`，
     文字写到 `reference_audio\\ref_text.txt`（UTF-8），之后就不用每次上传。
5. 把电子书（`.epub/.mobi/.azw3/.txt`）拖进界面或放进 `audiobook_tool\\input\\`：
   **上传即自动解析出章节**，勾选分册后开始生成。
   （「解析预览」只是**重新计算**按钮；只有改了选择、想重算时才需要点）

其他脚本：
| 文件 | 用途 |
|---|---|
| `自检.bat` | 检查路径解析、依赖、CUDA 是否就绪（出问题先跑它） |
| `停止.bat` | 关闭正在运行的服务（7860 端口） |
| `下载模型.bat` | {download_tip} |

---

## 二、包里有什么

```
{tree}
```

---

## 三、体积与两版差异

| 版本 | 解压后体积 | 压缩包 | 说明 |
|---|---|---|---|
| **lite** | ≈ {lite_raw} | ≈ {lite_zip} | 不含 TTS 权重（用 `下载模型.bat` 拉） |
| **full** | ≈ {full_raw} | ≈ {full_zip} | 含 Qwen3-TTS 权重，解压即能出声（仍需自备参考音频） |

两版差异**只有 `models\\{model_dir}\\`**（TTS 权重）；其余完全一致。
`models\\campplus.onnx`（声纹质检模型，27 MB）两版都有。

---

## 四、环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 64 位 |
| 显卡 | **NVIDIA**，显存 ≥ 6 GB（CPU 能跑但极慢）；驱动建议较新版本 |
| 磁盘 | full ≈ {full_raw} 解压 + 生成产物空间（一章约 5~15 MB） |
| 网络 | 只有 lite 首次下模型需要；生成过程完全离线 |

---

## 五、已知限制

1. **必须解压到可写目录**（见第一节）。只读目录会让首次 import 卡住。
2. 杀毒软件可能对首次启动做全量扫描（包里 `torch\\lib` 有近 4 GB DLL），
   第一次启动可能明显变慢，之后恢复正常。
3. **不附带参考音频**：第一次必须自己上传一段（或放进 `reference_audio\\`），
   否则无法开始生成。这是版权决定，不是 bug。
4. 模型加载 + 首段合成需要时间（本机 RTX 3080 ≈ {first_launch} 到界面、≈ {synth_time} 合成一小段）。
5. 多角色配音（`speaker.py`）尚未接入引擎，当前是单音色整本朗读。
6. 生成的书稿、输出 mp3 都在包内（`audiobook_tool\\output\\`），删包即全部删除。

---

## 六、版权与免责

- 本包**不含任何参考音频、示例语音或预填文本**：界面上所有可填项都是空的，
  参考音色与对应文字必须由使用者自行提供并自行确认授权。
- 请勿使用本工具克隆他人声音用于冒充、欺骗或任何违法用途。
- TTS 权重（Qwen3-TTS，Apache-2.0）、CAM++ 声纹（Apache-2.0）、ffmpeg（LGPL/GPL）与
  Python 依赖的授权声明见 `THIRD_PARTY_NOTICES.md` 与各包自带 LICENSE。
- 本发行包内含的 VC++ 运行库 DLL 来自 Microsoft Visual C++ Redistributable（允许随应用再分发）。

---

## 七、这份包的验证情况（重要，别当成"全验过了"）

**已实测通过**（Windows，{test_machine}）：

{verified}

**没能验证的**（本机条件限制，请知悉）：

{unverified}

---

构建时间：{build_time}
"""

DEFAULT_VERIFIED = """- ✅ 在 **D: 盘「测试 目录 空格」（中文 + 空格、非开发盘）** 解压后启动成功
- ✅ 清空 `AUDIOBOOK_*` 与 `PYTHONPATH`、PATH 只保留系统目录时：`sys.executable`、`config.FFMPEG`、
  `qwen_tts` 全部指向包内，`shutil.which("ffmpeg")` 为 `None`
- ✅ 首次启动到 `/api/bootstrap` 返回 200 用时 **4.1 秒**（本机 RTX 3080）
- ✅ **端到端真合成**：`scripts/smoke_test_engine.py`（2 段）耗时 **67 秒**（含模型加载）
- ✅ 产物 `mp3` 24 kHz 单声道 17.17 s，用**包内 ffmpeg 整文件解码 exit 0**（可完整播放）
- ✅ `selfcheck.py` 除「参考音频未就绪」外 0 项失败（参考音频按版权要求不附带）"""

DEFAULT_UNVERIFIED = """- ❌ **另一台物理电脑**：本机就是开发机，未在第二台机器上装过（这是「下载即用」最需要你复验的一条）
- ❌ **没有 NVIDIA 显卡 / 没装驱动的机器**：预期 `cuda=False`，自检会报 `!!`，生成不可用
- ❌ **杀毒软件首次全量扫描**的影响：包内 `torch\\lib` 约 3.8 GB DLL，首次启动可能被扫描拖慢
- ❌ **完全断网**下的首次启动（生成过程本身不联网，但未实测断网）
- ❌ 特殊路径：OneDrive 同步目录、UNC 网络路径未测（普通本地路径含中文/空格已测）"""

START_BAT = r'''@echo off
chcp 65001 >nul
setlocal enableextensions
set "ROOT=%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PORTARGS="
if not "%~1"=="" set "PORTARGS=--port %~1"
if not exist "%ROOT%python\python.exe" (
  echo [ERROR] python\python.exe not found - the package is incomplete.
  echo         Please extract ALL zip parts into the SAME folder.
  pause
  exit /b 1
)
if not exist "%ROOT%audiobook_tool\server.py" (
  echo [ERROR] audiobook_tool\server.py not found - the package is incomplete.
  pause
  exit /b 1
)
echo ============================================================
echo   Audiobook Tool  -  portable package
echo   python : %ROOT%python\python.exe
echo   app    : %ROOT%audiobook_tool\server.py
echo   url    : http://127.0.0.1:7860   (browser opens automatically)
echo   close this window to stop the server
echo ============================================================
cd /d "%ROOT%audiobook_tool"
"%ROOT%python\python.exe" server.py %PORTARGS%
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo.
  echo [ERROR] server exited with code %RC%
  if "%RC%"=="3" (
    echo         code 3 usually means PORT 7860 IS ALREADY IN USE:
    echo         another instance may still be running - close it, or run
    echo         stop.bat first, or start with another port:  start.bat 7861
  ) else (
    echo         run selfcheck.bat to see what is wrong.
  )
  pause
)
endlocal
'''

SELFCHECK_BAT = r'''@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYTHONIOENCODING=utf-8"
"%ROOT%python\python.exe" "%ROOT%selfcheck.py"
echo.
echo exit code = %ERRORLEVEL%
if /i "%~1"=="/q" exit /b %ERRORLEVEL%
pause
endlocal
'''

STOP_BAT = r'''@echo off
rem 关闭正在运行的 UI（默认端口 7860）。用法: 停止.bat [/q] [端口]
rem 用 %SystemRoot% 下的绝对路径调 PowerShell，避免依赖 PATH 里有没有 powershell
setlocal
set "PORT=7860"
set "QUIET="
for %%A in (%*) do (
  if /i "%%A"=="/q" (set "QUIET=1") else (set "PORT=%%A")
)
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
"%PS%" -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force; Write-Host ('stopped PID ' + $_.OwningProcess) }"
if defined QUIET exit /b 0
echo.
echo (nothing printed above = no server was running)
pause
endlocal
'''

DOWNLOAD_BAT = r'''@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PYTHONIOENCODING=utf-8"
"%ROOT%python\python.exe" "%ROOT%tools\download_model.py"
pause
endlocal
'''

DOWNLOAD_PY = '''# -*- coding: utf-8 -*-
"""下载 Qwen3-TTS-12Hz-0.6B-Base 权重到 <包根>\\models\\（lite 版首次使用前必跑）。

权重约 1.8 GB；huggingface.co 在国内通常不可达，所以走 ModelScope。
"""
import os
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(PKG, 'models', 'Qwen3-TTS-12Hz-0.6B-Base')

if os.path.isfile(os.path.join(DST, 'model.safetensors')):
    print('模型已存在，无需下载:', DST)
    sys.exit(0)

os.makedirs(os.path.dirname(DST), exist_ok=True)
try:
    from modelscope import snapshot_download
except Exception as e:                                    # noqa: BLE001
    print('无法导入 modelscope（包不完整？）:', e)
    sys.exit(1)

print('开始下载 Qwen3-TTS-12Hz-0.6B-Base（约 1.8 GB）...')
print('目标:', DST)
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base', local_dir=DST)
if os.path.isfile(os.path.join(DST, 'model.safetensors')):
    print('完成 ✅')
else:
    print('下载似乎未完成，请重跑本脚本')
    sys.exit(1)
'''

REF_README = """这个目录里放「参考音频」（克隆音色用）。

本发行包**不附带任何参考音频**（版权原因，README 里写明了要你自备）。

两种用法：
1. 想让工具默认就用某个音色：放一个 5~10 秒、单人、干净、已剪掉静音的语音，
   命名为 ref_tight.wav，并把与它**逐字一致**的文字写进 ref_text.txt（UTF-8）。
2. 单次使用：直接在网页界面上传音频 + 填文字即可，不用放这里。

注意：文字与音频不一致会让克隆出来的音色发飘。
请自行确认你拥有的音频素材的授权情况；请勿用于冒充他人或任何违法用途。
"""


# ─────────────────────────── 构建步骤 ───────────────────────────

def copy_python_base(base_py, tree):
    """复制底座解释器（去 Doc/include/libs/Scripts），并平铺开发 venv 的 site-packages。"""
    dst = os.path.join(tree, 'python')
    log('【1/8】复制底座解释器 %s' % base_py)
    n = robocopy(base_py, dst, xd=['Doc', 'include', 'libs', 'Scripts',
                                   os.path.join(base_py, 'Lib', 'test')])
    log(f'  → {n} 个文件, {human(dir_size(dst))}')
    return dst


def overlay_site_packages(venv, tree):
    """把开发 venv 的 site-packages 平铺进内置解释器（不用 venv：pyvenv.cfg 是绝对路径，搬了就坏）。"""
    sp = os.path.join(venv, 'Lib', 'site-packages')
    if not os.path.isdir(sp):
        die(f'开发 venv 里没有 site-packages: {sp}')
    dst = os.path.join(tree, 'python', 'Lib', 'site-packages')
    log('【2/8】平铺依赖 %s' % sp)
    rmtree_safe(dst, 'site-packages')
    os.makedirs(dst, exist_ok=True)
    robocopy(sp, dst)
    log(f'  → {count_files(dst)} 个文件, {human(dir_size(dst))}')


def install_qwen_tts_plain(repo, tree):
    """把 editable 安装换成实体包（editable 的 finder 里写死了开发机路径）。"""
    log('【3/8】替换 qwen_tts 的 editable 安装为实体包')
    sp = os.path.join(tree, 'python', 'Lib', 'site-packages')
    removed = []
    for f in os.listdir(sp):
        if f.startswith('__editable__'):
            p = os.path.join(sp, f)
            (shutil.rmtree if os.path.isdir(p) else os.remove)(p)
            removed.append(f)
    src = os.path.join(repo, 'vendor', 'Qwen3-TTS', 'qwen_tts')
    if not os.path.isdir(src):
        die(f'找不到 qwen_tts 源码: {src}')
    dst = os.path.join(sp, 'qwen_tts')
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # direct_url.json 里也带着开发机路径，一起清掉（纯元数据）
    du = os.path.join(sp, 'qwen_tts-0.1.1.dist-info', 'direct_url.json')
    if os.path.isfile(du):
        os.remove(du)
        removed.append('qwen_tts-0.1.1.dist-info/direct_url.json')
    log(f'  删除: {removed if removed else "无"}；实体包 → site-packages\\qwen_tts')


def copy_vc_runtime(tree):
    """补 VC++ 运行库（干净系统缺 msvcp140 会导致 import torch 失败）。"""
    log('【4/8】补 VC++ 运行库 DLL')
    dst = os.path.join(tree, 'python')
    got = []
    for f in VC_DLLS:
        s = os.path.join(SYSTEM32, f)
        if os.path.isfile(s):
            if not os.path.isfile(os.path.join(dst, f)):
                copy_file(s, os.path.join(dst, f))
                got.append(f)
    log(f'  → {got if got else "已全部存在"}')


def copy_app(repo, tree, with_dev_docs=False):
    """产品代码 + 工具脚本（产品代码原样复制，不改一字节）。

    **默认不随包分发任何开发文档**：docs/ 与 tts_eval/ 里含开发机上的书稿片段、
    书名、个人素材文件名与评测细节，发行包只带产品代码 + 面向使用者的《使用说明.md》。
    需要时用 --with-dev-docs 显式带上（那时仍会做开发机路径净化），仓库原文一个字不改。
    """
    log('【5/8】复制产品代码%s' % ('与开发文档' if with_dev_docs else '（不含任何开发文档）'))
    sanitized = 0
    app = os.path.join(tree, 'audiobook_tool')
    os.makedirs(app, exist_ok=True)
    for f in APP_FILES:
        s = os.path.join(repo, f)
        if not os.path.isfile(s):
            die(f'缺少产品文件: {s}')
        copy_file(s, os.path.join(app, f))
    for d in APP_DIRS:
        robocopy(os.path.join(repo, d), os.path.join(app, d))
    for d in TOOL_DIRS:
        robocopy(os.path.join(repo, d), os.path.join(app, d), xf=['ui.pid', 'uilog*.txt'],
                 xd=['_dev', '_legacy', '__pycache__'])
    for f in ROOT_FILES:
        s = os.path.join(repo, f)
        if os.path.isfile(s):
            copy_file(s, os.path.join(tree, f))
    if with_dev_docs:
        for f in DOC_FILES:
            sanitized += copy_doc(repo, f, tree, os.path.join('docs', os.path.basename(f)))
        for d in DOC_DIRS:
            s = os.path.join(repo, d)
            if os.path.isdir(s):
                robocopy(s, os.path.join(tree, d))
        sanitized += copy_doc(repo, 'AGENTS.md', tree, os.path.join('docs', 'AGENTS.md'))
        for f in EVAL_DOC:
            sanitized += copy_doc(repo, f, tree, f)      # 保持 tts_eval\ 目录结构
    # 空 input / output + 说明
    for d, note in (('input', '把你的 .epub/.mobi/.azw3/.txt 电子书放进这个目录，界面会自动识别。\n'),
                    ('output', '生成结果（章节 mp3 / 全书 mp3 / 播放清单 / 段级缓存）会写到这里。\n')):
        os.makedirs(os.path.join(app, d), exist_ok=True)
        with open(os.path.join(app, d, 'PUT_FILES_HERE.txt'), 'w', encoding='utf-8') as f:
            f.write(note)
    # 参考音频目录（空的，见 REF_README）
    os.makedirs(os.path.join(tree, 'reference_audio'), exist_ok=True)
    with open(os.path.join(tree, 'reference_audio', '请读我-参考音频说明.txt'), 'w',
              encoding='utf-8') as f:
        f.write(REF_README)
    log(f'  → audiobook_tool 代码 + scripts/{len(os.listdir(os.path.join(app, "scripts")))} 个工具 + 文档'
        f'（文档路径净化 {sanitized} 处）')
    return sanitized


def copy_assets(repo, tree, with_model):
    """models/bin/tools/启动器。"""
    log('【6/8】复制模型与二进制')
    models = os.path.join(tree, 'models')
    os.makedirs(models, exist_ok=True)
    camp = os.path.join(repo, CAMPP_REF)
    if not os.path.isfile(camp):
        die(f'缺少 {camp}')
    copy_file(camp, os.path.join(models, 'campplus.onnx'))
    if with_model:
        src = os.path.join(repo, MODEL_REL)
        if not os.path.isfile(os.path.join(src, 'model.safetensors')):
            die(f'缺少模型权重: {src}\\model.safetensors')
        robocopy(src, os.path.join(models, MODEL_DIRNAME), xd=['.git'],
                 xf=['.gitattributes'])
        log(f'  → models\\{MODEL_DIRNAME} = {human(dir_size(os.path.join(models, MODEL_DIRNAME)))}')
    else:
        log('  → 不含 TTS 权重（lite）')
    for exe in ('ffmpeg.exe', 'ffprobe.exe'):
        s = os.path.join(repo, 'bin', exe)
        if not os.path.isfile(s):
            die(f'缺少 {s}')
        copy_file(s, os.path.join(tree, 'bin', exe))
    tools = os.path.join(tree, 'tools')
    os.makedirs(tools, exist_ok=True)
    with open(os.path.join(tools, 'download_model.py'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write(DOWNLOAD_PY)
    for name in ('启动.bat', 'start.bat'):
        with open(os.path.join(tree, name), 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(START_BAT)
    for name in ('自检.bat', 'selfcheck.bat'):
        with open(os.path.join(tree, name), 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(SELFCHECK_BAT)
    for name in ('停止.bat', 'stop.bat'):
        with open(os.path.join(tree, name), 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(STOP_BAT)
    for name in ('下载模型.bat', 'download_model.bat'):
        with open(os.path.join(tree, name), 'w', encoding='utf-8', newline='\r\n') as f:
            f.write(DOWNLOAD_BAT)
    log('  → ffmpeg/ffprobe + 启动器 + tools\\download_model.py')


def python_version_of(tree):
    exe = os.path.join(tree, 'python', 'python.exe')
    p = subprocess.run([exe, '-c', 'import sys;print(sys.version.split()[0])'],
                       capture_output=True, text=True)
    return p.stdout.strip() or '?'


def scan_dev_paths(tree):
    """全树扫描开发机路径指纹；返回 [(相对路径, 行号, 片段), ...]。"""
    hits = []
    for root, _, files in os.walk(tree):
        for f in files:
            p = os.path.join(root, f)
            if os.path.splitext(f)[1].lower() not in TEXT_EXT:
                continue
            try:
                if os.path.getsize(p) > SCAN_LIMIT:
                    continue
                with open(p, encoding='utf-8', errors='ignore') as fh:
                    for i, line in enumerate(fh, 1):
                        for pat in DEV_PATH_PATTERNS:
                            if pat.search(line):
                                hits.append((os.path.relpath(p, tree), i, line.strip()[:120]))
                                break
            except OSError:
                pass
    return hits


def scan_pth(tree):
    """site-packages 里的 .pth 不许写绝对路径（editable 那类）。"""
    sp = os.path.join(tree, 'python', 'Lib', 'site-packages')
    bad = []
    for f in os.listdir(sp):
        if f.endswith('.pth'):
            with open(os.path.join(sp, f), encoding='utf-8', errors='ignore') as fh:
                for line in fh:
                    s = line.strip()
                    if not s or s.startswith('#') or s.startswith('import '):
                        continue
                    if os.path.isabs(s) or ':' in s:
                        bad.append((f, s[:120]))
    return bad


REQUIRED = [
    'python/python.exe',
    'python/Lib/site-packages/torch/__init__.py',
    'python/Lib/site-packages/qwen_tts/__init__.py',
    'python/Lib/site-packages/fastapi/__init__.py',
    'python/Lib/site-packages/onnxruntime/__init__.py',
    'audiobook_tool/server.py',
    'audiobook_tool/config.py',
    'audiobook_tool/static/index.html',
    'audiobook_tool/scripts/smoke_test_engine.py',
    'models/campplus.onnx',
    'bin/ffmpeg.exe',
    'bin/ffprobe.exe',
    'selfcheck.py',
    'tools/download_model.py',
    '启动.bat',
    'start.bat',
    '自检.bat',
    '停止.bat',
]


def audit_user_content(tree, with_dev_docs=False):
    """发行包里不许出现使用者自备内容（示例语音 / 参考台词 / 个人素材文件名 / 开发文档）。

    注意：这里**不把参考台词原文写进脚本**（否则脚本本身就把台词带进仓库了），
    只按「文件类型 + 素材命名 + 目录」做结构性检查。
    """
    problems = []
    site_prefix = os.path.join('python', 'Lib', 'site-packages')
    audio_ext = {'.wav', '.mp3', '.m4a', '.flac', '.aac', '.ogg'}
    for root, _, files in os.walk(tree):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, tree)
            in_site = rel.lower().startswith(site_prefix.lower())
            in_stdlib_test = rel.lower().startswith(os.path.join('python', 'lib', 'test').lower())
            low = f.lower()
            if os.path.splitext(low)[1] in audio_ext and not (in_site or in_stdlib_test):
                problems.append(f'包内出现音频文件: {rel}')
            if low in ('ref_text.txt',) or low.startswith('ref_') and os.path.splitext(low)[1] in ('.wav', '.m4a'):
                problems.append(f'包内出现参考素材: {rel}')
            if 'perlica' in low:
                problems.append(f'包内出现个人素材名: {rel}')
    if not with_dev_docs:
        for d in ('tts_eval', os.path.join('docs', 'screenshots')):
            if os.path.exists(os.path.join(tree, d)):
                problems.append(f'默认不应随包的开发文档目录存在: {d}')
    return problems


def verify_tree(tree, with_model, repo=None, with_dev_docs=False):
    """构建产物自检：必需文件 + 开发机路径指纹 + .pth 绝对路径 + 大文件体积。"""
    log('【7/8】校验构建产物')
    missing = [r for r in REQUIRED if not os.path.exists(os.path.join(tree, r))]
    if with_model:
        if not os.path.isfile(os.path.join(tree, 'models', MODEL_DIRNAME, 'model.safetensors')):
            missing.append('models/%s/model.safetensors' % MODEL_DIRNAME)
    else:
        if os.path.isdir(os.path.join(tree, 'models', MODEL_DIRNAME)):
            missing.append('!! lite 版里不应存在 models/%s' % MODEL_DIRNAME)
    if missing:
        die('缺少必需文件:\n  ' + '\n  '.join(missing))
    # 大文件体积必须与源一致（防复制截断）
    bad_size = []
    if repo:
        pairs = [(CAMPP_REF, os.path.join('models', 'campplus.onnx')),
                 (os.path.join('bin', 'ffmpeg.exe'), os.path.join('bin', 'ffmpeg.exe')),
                 (os.path.join('bin', 'ffprobe.exe'), os.path.join('bin', 'ffprobe.exe'))]
        if with_model:
            pairs.append((os.path.join(MODEL_REL, 'model.safetensors'),
                          os.path.join('models', MODEL_DIRNAME, 'model.safetensors')))
        for src_rel, dst_rel in pairs:
            s, d = os.path.join(repo, src_rel), os.path.join(tree, dst_rel)
            if os.path.getsize(s) != os.path.getsize(d):
                bad_size.append(f'{dst_rel}: {os.path.getsize(d)} != {os.path.getsize(s)}')
    if bad_size:
        die('复制后的文件体积与源不一致:\n  ' + '\n  '.join(bad_size))
    hits = scan_dev_paths(tree)
    pthbad = scan_pth(tree)
    userstuff = audit_user_content(tree, with_dev_docs)
    log(f'  必需文件: 全部存在（{len(REQUIRED) + (1 if with_model else 0)} 项）；大文件体积与源一致')
    log(f'  开发机路径指纹: {len(hits)} 处 {hits[:6] if hits else "✅"}')
    log(f'  .pth 绝对路径: {len(pthbad)} 处 {pthbad[:4] if pthbad else "✅"}')
    log(f'  用户自定义内容: {len(userstuff)} 处 {userstuff[:6] if userstuff else "✅ 无示例语音/参考素材/开发文档"}')
    if hits or pthbad or userstuff:
        die('构建产物未通过校验，已中止（见上）')
    return {'dev_path_hits': hits, 'pth_bad': pthbad, 'user_content': userstuff}


# ─────────────────────────── 打包 ───────────────────────────

def list_entries(tree):
    out = []
    for root, _, files in os.walk(tree):
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), tree).replace(os.sep, '/'))
    return sorted(out)


def make_zip(tree, entries, zpath, compresslevel=6, prefix='audiobook-tool-portable/'):
    total = sum(os.path.getsize(os.path.join(tree, e.replace('/', os.sep))) for e in entries)
    log(f'  打包 {os.path.basename(zpath)}：{len(entries)} 个文件, {human(total)} → deflate level {compresslevel}')
    done = 0
    nxt = 0
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED,
                         compresslevel=compresslevel, allowZip64=True) as z:
        for e in entries:
            p = os.path.join(tree, e.replace('/', os.sep))
            z.write(p, prefix + e)
            done += os.path.getsize(p)
            if done >= nxt:
                log(f'    ... {human(done)} / {human(total)}')
                nxt = done + 500 * 1024 * 1024
    return os.path.getsize(zpath)


def sha256(path, chunk=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# 各扩展名的实测/保守压缩率（zip deflate level 6）：torch 大 DLL 实测 0.64
COMPRESS_RATIO = {'.dll': 0.68, '.pyd': 0.68, '.exe': 0.68, '.pyc': 0.45,
                  '.py': 0.40, '.json': 0.30, '.safetensors': 0.98}


def est_compressed(path):
    ext = os.path.splitext(path)[1].lower()
    return os.path.getsize(path) * COMPRESS_RATIO.get(ext, 0.55)


def plan_parts(tree, entries, cap_gb, margin=0.90):
    """按「预估压缩后大小」把文件装箱分片，保证每片预计 < cap_gb（GitHub 单文件 2GiB 上限）。

    逐文件贪心累积（不按目录整块搬，否则 torch\\lib 这种 3.8GB 的目录会撑爆一片）。
    文件按目录名排序处理，同一目录的文件尽量落在相邻位置。
    """
    target = cap_gb * margin * 1024 ** 3
    ordered = sorted(entries, key=lambda e: (e.rsplit('/', 1)[0], e))
    parts, cur, cur_est = [], [], 0.0
    for e in ordered:
        est = est_compressed(os.path.join(tree, e.replace('/', os.sep)))
        if cur and cur_est + est > target:
            parts.append(cur)
            cur, cur_est = [], 0.0
        cur.append(e)
        cur_est += est
    if cur:
        parts.append(cur)
    log(f'  分片: 目标每片预估 <{human(target)} → {len(parts)} 片；各片预估压缩后体积 '
        + ', '.join(human(sum(est_compressed(os.path.join(tree, e.replace("/", os.sep)))
                              for e in p)) for p in parts))
    return parts, [sum(os.path.getsize(os.path.join(tree, e.replace('/', os.sep))) for e in p)
                   for p in parts]


def write_zips(tree, out, stem, entries, split, also_split, cap_gb, result):
    """出包：单片 zip，和/或 GitHub 用的分片 zip（同名 partNofM）。"""
    plans = []
    if not split:
        plans.append(('single', [entries]))
    if split or also_split:
        parts, _ = plan_parts(tree, entries, cap_gb)
        plans.append(('parts', parts))
    for kind, group in plans:
        for i, ent in enumerate(group, 1):
            if kind == 'single':
                name = f'{stem}.zip'
            else:
                name = f'{stem}.part{i}of{len(group)}.zip'
            zp = os.path.join(out, name)
            size = make_zip(tree, ent, zp)
            result['zips'].append({'name': name, 'bytes': size, 'sha256': sha256(zp)})
            log(f'  → {name}: {human(size)}')
            if kind == 'parts' and size > 2 * 1024 ** 3:
                log(f'  !! 注意: {name} 超过 GitHub 的 2 GiB 单文件上限，请用更小的 --split-cap 重跑')


# ─────────────────────────── 主流程 ───────────────────────────

def build_readme(tree, edition, sizes, verified, unverified):
    tree_txt = '\n'.join(tree_lines(tree))          # 现算：调用时树已经建好了
    first_launch = sizes.get('first_launch', '（未实测）')
    synth_time = sizes.get('synth_time', '（未实测）')
    test_machine = sizes.get('test_machine', '（未记录）')
    txt = README_TMPL.format(
        edition='lite 版（不含 TTS 权重）' if edition == 'lite' else 'full 版（含 TTS 权重）',
        model_step=('**先双击 `下载模型.bat`** 下载 TTS 权重（约 1.8 GB，需要能访问 modelscope.cn），'
                    '下完再启动。' if edition == 'lite'
                    else '双击 `启动.bat` 直接开始（权重已随包附带）。'),
        download_tip=('下载 TTS 权重（约 1.8 GB，需要能访问 modelscope.cn）' if edition == 'lite'
                      else '本包已含权重，一般用不到；模型缺失时可用它重新下载'),
        tree=tree_txt,
        lite_raw=sizes['lite_raw'], lite_zip=sizes['lite_zip'],
        full_raw=sizes['full_raw'], full_zip=sizes['full_zip'],
        model_dir=MODEL_DIRNAME,
        first_launch=first_launch, synth_time=synth_time,
        test_machine=test_machine,
        verified=verified, unverified=unverified,
        build_time=time.strftime('%Y-%m-%d %H:%M:%S'),
    )
    with open(os.path.join(tree, '使用说明.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)


def tree_lines(tree):
    lines = []
    for name in sorted(os.listdir(tree)):
        p = os.path.join(tree, name)
        if os.path.isdir(p):
            lines.append(f'  {name}\\   {human(dir_size(p))}')
        else:
            lines.append(f'  {name}   {human(os.path.getsize(p))}')
    return lines


def main():
    ap = argparse.ArgumentParser(description='构建 audiobook_tool 自包含 Windows 发行包')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--lite', action='store_true', help='只构建 lite（不含 TTS 权重）')
    g.add_argument('--full', action='store_true', help='只构建 full（含 TTS 权重）')
    g.add_argument('--both', action='store_true', help='先 full 再派生 lite（默认）')
    ap.add_argument('--zip', action='store_true', help='同时打 zip')
    ap.add_argument('--split', action='store_true',
                    help='只出分片 zip（每片预计 <2GiB，GitHub Release 用）')
    ap.add_argument('--also-split', action='store_true',
                    help='在单片 zip 之外，额外再出一份分片 zip（两处都要发时用）')
    ap.add_argument('--split-cap', type=float, default=1.9,
                    help='单片压缩后上限（GB，默认 1.9）')
    ap.add_argument('--venv', default='')
    ap.add_argument('--base-python', default='')
    ap.add_argument('--repo', default=REPO)
    ap.add_argument('--stage', default=os.environ.get('AUDIOBOOK_BUILD_STAGE', DEFAULT_STAGE))
    ap.add_argument('--out', default=os.environ.get('AUDIOBOOK_BUILD_OUT', DEFAULT_OUT))
    ap.add_argument('--facts', default='',
                    help='注入到包内《使用说明.md》的实测数据（JSON，键: lite_raw/lite_zip/'
                         'full_raw/full_zip/first_launch/synth_time/test_machine/verified/unverified）')
    ap.add_argument('--with-dev-docs', action='store_true',
                    help='随包带上开发文档（docs/ 与 tts_eval/）。默认不带：那些文档含开发机上的'
                         '书稿片段、书名与个人素材文件名，不适合公开分发')
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    venv = find_venv(args.venv)
    base_py = find_base_python(venv, args.base_python)
    tree = os.path.join(os.path.abspath(args.stage), 'audiobook-tool-portable')
    out = os.path.abspath(args.out)

    log('=== audiobook_tool 便携包构建 ===')
    log(f'仓库      : {repo}')
    log(f'开发 venv : {venv}')
    log(f'底座解释器: {base_py}')
    log(f'构建树    : {tree}')
    log(f'产物目录  : {out}')
    for p in (repo, venv, base_py):
        if not os.path.isdir(p):
            die(f'路径不存在: {p}')

    rmtree_safe(tree, 'portable_build')
    os.makedirs(tree, exist_ok=True)
    os.makedirs(out, exist_ok=True)

    with_model = not args.lite
    facts = {}
    if args.facts and os.path.isfile(args.facts):
        with open(args.facts, encoding='utf-8-sig') as f:
            facts = json.load(f)
        log(f'注入实测数据: {args.facts}')
    verified = facts.get('verified') or DEFAULT_VERIFIED
    unverified = facts.get('unverified') or DEFAULT_UNVERIFIED
    sizes = {'tree_lines': tree_lines(tree),
             'lite_raw': facts.get('lite_raw', '≈5.12 GiB'),
             'lite_zip': facts.get('lite_zip', '≈2.87 GiB（分两片）'),
             'full_raw': facts.get('full_raw', '≈7.47 GiB'),
             'full_zip': facts.get('full_zip', '≈4.78 GiB'),
             'first_launch': facts.get('first_launch', '未记录'),
             'synth_time': facts.get('synth_time', '未记录'),
             'test_machine': facts.get('test_machine', '未记录')}
    copy_python_base(base_py, tree)
    overlay_site_packages(venv, tree)
    install_qwen_tts_plain(repo, tree)
    copy_vc_runtime(tree)
    sanitized = copy_app(repo, tree, args.with_dev_docs)
    copy_assets(repo, tree, with_model)
    build_readme(tree, 'full' if with_model else 'lite', sizes, verified, unverified)
    vr = verify_tree(tree, with_model, repo, args.with_dev_docs)
    raw = dir_size(tree)
    log('【8/8】构建完成')

    result = {
        'tree': tree, 'edition': 'full' if with_model else 'lite',
        'raw_bytes': raw, 'files': count_files(tree),
        'python': python_version_of(tree),
        'verify': {'dev_path_hits': len(vr['dev_path_hits']), 'pth_bad': len(vr['pth_bad']),
                   'user_content': len(vr['user_content'])},
        'doc_sanitized': sanitized,
        'zips': [],
    }
    log(f'内置 Python : {result["python"]}')
    log(f'树体积      : {human(raw)} / {result["files"]} 个文件')

    if args.zip:
        stem = 'audiobook-tool-portable-full' if with_model else 'audiobook-tool-portable-lite'
        entries = list_entries(tree)
        write_zips(tree, out, stem, entries, args.split, args.also_split,
                   args.split_cap, result)
        # full 打完 → 就地派生 lite（同一个树，删掉权重再打）
        if args.both:
            md = os.path.join(tree, 'models', MODEL_DIRNAME)
            if os.path.isdir(md):
                rmtree_safe(md, MODEL_DIRNAME)
            if os.path.isdir(md):
                die('模型目录没能删掉，lite 版不干净')
            log('  派生 lite：模型权重已移除，其余与 full 完全一致')
            build_readme(tree, 'lite',
                         {'tree_lines': tree_lines(tree),
                          'lite_raw': sizes['lite_raw'], 'lite_zip': sizes['lite_zip'],
                          'full_raw': sizes['full_raw'], 'full_zip': sizes['full_zip'],
                          'first_launch': sizes['first_launch'],
                          'synth_time': sizes['synth_time'],
                          'test_machine': sizes['test_machine']},
                         verified, unverified)
            entries = list_entries(tree)
            write_zips(tree, out, 'audiobook-tool-portable-lite', entries,
                       args.split, args.also_split, args.split_cap, result)

    with open(os.path.join(out, 'build-result.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log('--- 结果 ---')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
