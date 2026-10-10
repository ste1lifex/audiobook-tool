# start_ui.ps1 - 启动有声书工具的 Web UI（后台运行）
# 可移植版：Python 解释器按以下顺序查找，不再写死路径
#   1) 环境变量 AUDIOBOOK_PYTHON
#   2) <包根>\envs\audiobook\Scripts\python.exe   （setup.ps1 默认装在这里）
#   3) <包根>\..\envs\audiobook\Scripts\python.exe
#   4) PATH 里的 python
# 幂等：若已在运行，只打开浏览器。
# NOTE: keep this file ASCII-only to avoid codepage issues with Windows PowerShell 5.1.
$ErrorActionPreference = 'SilentlyContinue'

$Root   = $PSScriptRoot
$Pkg    = Split-Path $Root -Parent
$App    = Join-Path $Root 'server.py'
$PidF   = Join-Path $Root 'ui.pid'
$LogOut = Join-Path $Root 'uilog.txt'
$LogErr = Join-Path $Root 'uilog_err.txt'
$Url    = 'http://127.0.0.1:7860'

function Resolve-Python {
    if ($env:AUDIOBOOK_PYTHON -and (Test-Path $env:AUDIOBOOK_PYTHON)) { return $env:AUDIOBOOK_PYTHON }
    $cands = @(
        (Join-Path $Pkg 'envs\audiobook\Scripts\python.exe'),
        (Join-Path (Split-Path $Pkg -Parent) 'envs\audiobook\Scripts\python.exe'),
        (Join-Path $Pkg '.venv\Scripts\python.exe'),
        (Join-Path $Pkg 'envs\qwen3tts\Scripts\python.exe')
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Write-Host '[1/3] Checking running UI...'
if (Test-Path $PidF) {
    $old = (Get-Content $PidF -Raw).Trim()
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Host "  UI already running (PID $old), opening browser."
        Start-Process $Url
        exit 0
    }
    Remove-Item $PidF -Force
}
$lst = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
if ($lst) {
    Write-Host "  Port 7860 busy (PID $($lst[0].OwningProcess)), opening browser."
    Start-Process $Url
    exit 0
}

$Py = Resolve-Python
if (-not $Py) {
    Write-Host '  ERROR: python not found.'
    Write-Host '  Set AUDIOBOOK_PYTHON, or run setup.ps1 first (it creates envs\audiobook).'
    exit 1
}

Write-Host "[2/3] Starting... $Py $App"
$p = Start-Process -FilePath $Py -ArgumentList @($App,'--no-browser') -WorkingDirectory $Root `
    -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr `
    -WindowStyle Hidden -PassThru

Start-Sleep -Seconds 10
# venv python 可能是 stub，真正监听端口的是 base 解释器，所以按端口找真实 PID
$pidReal = $null
for ($i = 0; $i -lt 8 -and -not $pidReal; $i++) {
    $lst = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
    if ($lst) { $pidReal = $lst[0].OwningProcess } else { Start-Sleep -Seconds 2 }
}
if ($pidReal) {
    $pidReal | Out-File -Encoding ascii $PidF
    Write-Host "  PID (listener) = $pidReal"
} else {
    $p.Id | Out-File -Encoding ascii $PidF
    Write-Host "  PID (started)  = $($p.Id)"
}

try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 8
    Write-Host "  OK: HTTP $($r.StatusCode)"
    Start-Process $Url
} catch {
    Write-Host "  WARN: UI not responding yet, check $LogErr"
    Write-Host "  PID saved to $PidF"
}
Write-Host '[3/3] Done.'
