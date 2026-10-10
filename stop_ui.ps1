# stop_ui.ps1 - 停止有声书工具的 Web UI
# NOTE: keep this file ASCII-only to avoid codepage issues with Windows PowerShell 5.1.
$ErrorActionPreference = 'SilentlyContinue'
$Root = $PSScriptRoot
$PidF = Join-Path $Root 'ui.pid'

Write-Host '[1/2] Stopping UI by PID...'
if (Test-Path $PidF) {
    $pidStr = (Get-Content $PidF -Raw).Trim()
    if ($pidStr) {
        $proc = Get-Process -Id $pidStr -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Killing PID $pidStr ($($proc.ProcessName))"
            Stop-Process -Id $pidStr -Force
        } else {
            Write-Host "  PID $pidStr not running, skip."
        }
    }
    Remove-Item $PidF -Force
} else {
    Write-Host '  No ui.pid found.'
}

Write-Host '[2/2] Cleanup port 7860...'
$lst = Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue
if ($lst) {
    foreach ($c in $lst) {
        Write-Host "  Killing listener PID $($c.OwningProcess)"
        Stop-Process -Id $c.OwningProcess -Force
    }
} else {
    Write-Host '  Port 7860 already free.'
}
Write-Host 'Done. UI stopped.'
