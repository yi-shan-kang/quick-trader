# Qixing Gaozhao scheduled tasks setup (PowerShell version)
# LF/UTF-8 friendly, uses Register-ScheduledTask (no schtasks quirks)
#
# Run in an ELEVATED PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup_scheduled_tasks.ps1
#   (or)  .\setup_scheduled_tasks.ps1  after Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$SimScript = Join-Path $Root 'start_qixing_sim.bat'
$QmtExe = 'D:\国金QMT交易端模拟New\bin.x64\XtMiniQmt.exe'

Write-Host '=== Setting up scheduled tasks ===' -ForegroundColor Cyan

# ---------- Task 1: SIM trading, weekdays 09:15 -> 15:05 ----------
Write-Host '[1/3] QixingGaozhao_SimTrading ...' -ForegroundColor Yellow

$action1 = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/c `"$SimScript`"" `
    -WorkingDirectory $Root

$trigger1 = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
    -At '09:15'

# ExecutionTimeLimit = 5h50m (09:15 + 5h50m = 15:05 auto-stop fallback).
# The strategy itself also exits at 15:05 via main.py sim mode.
$settings1 = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5 -Minutes 50)

Register-ScheduledTask -TaskName 'QixingGaozhao_SimTrading' `
    -Action $action1 -Trigger $trigger1 -Settings $settings1 `
    -RunLevel Highest -Force | Out-Null

Write-Host '      [OK] QixingGaozhao_SimTrading created' -ForegroundColor Green

# ---------- Task 2: QMT MiniQMT auto-start, logon + 30s ----------
Write-Host '[2/3] QMT_MiniQMT_AutoStart ...' -ForegroundColor Yellow

if (-not (Test-Path $QmtExe)) {
    Write-Warning "QMT exe not found: $QmtExe (task still created; verify path)"
}

$action2 = New-ScheduledTaskAction -Execute $QmtExe `
    -WorkingDirectory (Split-Path -Parent $QmtExe)

$trigger2 = New-ScheduledTaskTrigger -AtLogOn
$trigger2.Delay = 'PT30S'

$settings2 = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask -TaskName 'QMT_MiniQMT_AutoStart' `
    -Action $action2 -Trigger $trigger2 -Settings $settings2 `
    -RunLevel Highest -Force | Out-Null

Write-Host '      [OK] QMT_MiniQMT_AutoStart created' -ForegroundColor Green

# ---------- Task 3: Strategy watchdog, every 2 minutes ----------
# 独立进程检查策略心跳（logs/instances/*/heartbeat.json），
# 进程消失或心跳超时经 monitor/alerter 推送飞书/企业微信告警。
Write-Host '[3/3] QixingGaozhao_Watchdog ...' -ForegroundColor Yellow

$PyExe = if ($env:VIRTUAL_ENV) { Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' } else { 'python' }
$WatchdogScript = Join-Path $Root 'monitor\watchdog.py'

$action3 = New-ScheduledTaskAction -Execute $PyExe `
    -Argument "-m monitor.watchdog --once --timeout 180" `
    -WorkingDirectory $Root

# Daily 触发器 + 2 分钟重复一次，持续一年（PS 7+ 不允许无穷大）
$trigger3 = New-ScheduledTaskTrigger -Daily -At '00:00' `
    -RepetitionInterval (New-TimeSpan -Minutes 2) `
    -RepetitionDuration (New-TimeSpan -Days 365)

$settings3 = New-ScheduledTaskSettingsSet -StartWhenAvailable

Register-ScheduledTask -TaskName 'QixingGaozhao_Watchdog' `
    -Action $action3 -Trigger $trigger3 -Settings $settings3 `
    -RunLevel Highest -Force | Out-Null

Write-Host '      [OK] QixingGaozhao_Watchdog created' -ForegroundColor Green

Write-Host ''
Write-Host 'Done. Verify with:' -ForegroundColor Cyan
Write-Host '  Get-ScheduledTask -TaskName "QixingGaozhao_SimTrading"'
Write-Host '  Get-ScheduledTask -TaskName "QMT_MiniQMT_AutoStart"'
Write-Host '  Get-ScheduledTask -TaskName "QixingGaozhao_Watchdog"'
