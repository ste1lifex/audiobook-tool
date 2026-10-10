# =============================================================================
#  有声书生成工具 —— 一键安装脚本（在新电脑上运行）
#
#  做四件事：
#    1) 用 Python 3.12 建一个独立 venv（<包根>\envs\audiobook）
#    2) 装依赖（含 CUDA 版 torch）
#    3) 安装自带的 qwen_tts 源码
#    4) 从 ModelScope 下载 Qwen3-TTS-12Hz-0.6B-Base 权重（约 2.3GB）
#    5) 自检并报告
#
#  用法（在 PowerShell 里，于本文件所在目录）：
#      powershell -ExecutionPolicy Bypass -File .\setup.ps1
#      powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SkipModel   # 暂不下载模型
#      powershell -ExecutionPolicy Bypass -File .\setup.ps1 -SelfTest    # 装完顺便加载模型验证
# =============================================================================
param(
    [string]$PythonExe = '',        # 指定 Python 3.12 解释器；默认自动找 py -3.12 / python
    [switch]$SkipModel,             # 跳过模型下载
    [switch]$SelfTest,              # 额外做一次模型加载自检（约 30 秒）
    [string]$PipIndex = 'https://pypi.tuna.tsinghua.edu.cn/simple',   # 国内镜像，慢可换
    [string]$TorchIndex = 'https://download.pytorch.org/whl/cu126'
)

$ErrorActionPreference = 'Stop'
# 兼容两种布局：
#   ① 仓库布局  —— 本脚本与 server.py 同级（仓库根就是 audiobook_tool/）
#                  → 模型/venv 放在仓库内 <root>/models、<root>/envs
#   ② 发行包布局 —— 本脚本在 <包根>/audiobook_tool/ 里
#                  → 模型/venv 放在 <包根>/models、<包根>/envs
$Here = $PSScriptRoot
if (Test-Path (Join-Path $Here 'server.py')) { $Pkg = $Here }
else { $Pkg = Split-Path -Parent $Here }
$Venv = Join-Path $Pkg 'envs\audiobook'
$Py   = Join-Path $Venv 'Scripts\python.exe'
$Models = Join-Path $Pkg 'models'
$ModelDir = Join-Path $Models 'Qwen3-TTS-12Hz-0.6B-Base'

function Step($n, $t) { Write-Host "`n=== [$n] $t ===" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  WARN $m" -ForegroundColor Yellow }

Step '1/5' '检查 Python'
if (-not $PythonExe) {
    # 优先 py -3.12，其次 python
    $cand = @()
    try { $v = & py -3.12 -c "import sys;print(sys.version.split()[0])" 2>$null; if ($v) { $PythonExe = 'py'; $pyArgs = @('-3.12') } } catch {}
    if (-not $PythonExe) {
        $c = Get-Command python -ErrorAction SilentlyContinue
        if ($c) { $PythonExe = $c.Source; $pyArgs = @() }
    }
} else { $pyArgs = @() }
if (-not $PythonExe) { throw '找不到 Python。请安装 Python 3.12 并加入 PATH，或用 -PythonExe 指定完整路径。' }
if ($PythonExe -eq 'py') { & py -3.12 -c "import sys;print('  Python', sys.version.split()[0], '->', sys.executable)" }
else { & $PythonExe -c "import sys;print('  Python', sys.version.split()[0], '->', sys.executable)" }
Ok "解释器: $PythonExe $($pyArgs -join ' ')"

Step '2/5' '创建 venv（若已存在则复用）'
if (-not (Test-Path $Py)) {
    & $PythonExe @pyArgs -m venv $Venv
    if (-not (Test-Path $Py)) { throw "venv 创建失败: $Venv" }
    Ok "已创建 $Venv"
} else { Ok "已存在，复用 $Venv" }

Step '3/5' '安装依赖'
& $Py -m pip install -q --upgrade pip -i $PipIndex
Write-Host '  - CUDA 版 torch（约 2.5GB，最慢的一步）...'
& $Py -m pip install -q torch==2.13.0 torchaudio==2.11.0 --index-url $TorchIndex
Write-Host '  - 其余依赖...'
& $Py -m pip install -q -r (Join-Path $Pkg 'requirements.txt') -i $PipIndex
Write-Host '  - qwen_tts 源码（本包自带 vendor/Qwen3-TTS）...'
& $Py -m pip install -q -e (Join-Path $Pkg 'vendor\Qwen3-TTS')
Ok '依赖安装完成'

Step '4/5' '准备模型'
if ($SkipModel) { Warn '已按 -SkipModel 跳过' }
elseif (Test-Path (Join-Path $ModelDir 'model.safetensors')) { Ok "模型已存在: $ModelDir" }
else {
    Write-Host '  从 ModelScope 下载 Qwen3-TTS-12Hz-0.6B-Base（约 2.3GB）...'
    & $Py -c @"
import os
from modelscope import snapshot_download
dst = r'$ModelDir'
os.makedirs(os.path.dirname(dst), exist_ok=True)
snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-Base', local_dir=dst)
print('done')
"@
    if (Test-Path (Join-Path $ModelDir 'model.safetensors')) { Ok "模型就绪: $ModelDir" }
    else { Warn "下载似乎未完成，请重跑本脚本或手工放到 $ModelDir" }
}

Step '5/5' '自检'
$check = Join-Path $Pkg 'selfcheck.py'
if (-not (Test-Path $check)) { $check = Join-Path $Pkg 'audiobook_tool\selfcheck.py' }
if (Test-Path $check) {
    & $Py $check
    if ($LASTEXITCODE -ne 0) { Warn '自检有未通过项，请按上面 MISS / !! 逐条排查' }
    else { Ok '自检全部通过' }
} else { Warn '未找到 selfcheck.py，跳过' }

if ($SelfTest) {
    Write-Host "`n=== 附加自检：加载模型并合成一句话（约 30 秒） ==="
    $st = Join-Path $Pkg 'selftest_model.py'
    if (-not (Test-Path $st)) { $st = Join-Path $Pkg 'audiobook_tool\selftest_model.py' }
    if (Test-Path $st) {
        & $Py $st
        if (Test-Path (Join-Path $Pkg '_selftest.wav')) { Ok '模型自检通过（可播放 _selftest.wav 听一下）' }
    } else { Warn '未找到 selftest_model.py，跳过' }
}

Write-Host "`n安装流程结束。启动界面：" -ForegroundColor Cyan
Write-Host "    powershell -ExecutionPolicy Bypass -File `"$Pkg\audiobook_tool\start_ui.ps1`""
Write-Host "  或双击: $Pkg\audiobook_tool\start_ui.bat"
Write-Host "  界面地址: http://127.0.0.1:7860`n"
