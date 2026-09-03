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
Write-Host '[1/2] QixingGaozhao_SimTrading ...' -ForegroundColor Yellow

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
Write-Host '[2/2] QMT_MiniQMT_AutoStart ...' -ForegroundColor Yellow

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

Write-Host ''
Write-Host 'Done. Verify with:' -ForegroundColor Cyan
Write-Host '  Get-ScheduledTask -TaskName "QixingGaozhao_SimTrading"'
Write-Host '  Get-ScheduledTask -TaskName "QMT_MiniQMT_AutoStart"'
