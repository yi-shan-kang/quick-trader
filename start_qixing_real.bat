@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Qixing Gaozhao ETF Rotation - REAL (live) trading launcher
REM  Uses instances mode with REAL account (from config/account.local.json, not in repo).
REM  claim_existing_positions=true -> uses REAL available cash.
REM  Execute once at startup, then daily rebalance at 14:00.
REM  WARNING: LIVE TRADING - real money orders!
REM  NOTE: instances mode has NO 15:05 auto-exit (second Ctrl+C).
REM ============================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM ---------------- Config (edit if needed) ----------------
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "INSTANCES_CFG=%ROOT%config\instances_real_config.json"
set "STRATEGY=qixing_gaozhao"

REM ---------------- Logging ----------------
set "LOGDIR=%ROOT%LOG"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "DS=%date:~0,4%%date:~5,2%%date:~8,2%"
set "LOG=%LOGDIR%\real_%STRATEGY%_%DS%.log"

echo [%date% %time%] ==== QixingGaozhao REAL launcher start ==== >> "%LOG%"

REM ---------------- Pre-flight checks ----------------
if not exist "%PYTHON%" (
    echo [ERROR] venv python not found: %PYTHON%
    echo [ERROR] venv python not found: %PYTHON% >> "%LOG%"
    exit /b 1
)
if not exist "%INSTANCES_CFG%" (
    echo [ERROR] instances config not found: %INSTANCES_CFG%
    echo [ERROR] instances config not found: %INSTANCES_CFG% >> "%LOG%"
    exit /b 1
)
if not exist "%ROOT%main.py" (
    echo [ERROR] main.py not found: %ROOT%main.py
    echo [ERROR] main.py not found: %ROOT%main.py >> "%LOG%"
    exit /b 1
)

REM ---------------- Launch ----------------
echo [INFO] Log file: %LOG%
echo [INFO] Launching REAL trading (instances mode, no 15:05 auto-exit)...
echo [WARN] LIVE TRADING (real account from config/account.local.json). Ctrl+C twice to stop.

"%PYTHON%" -u main.py --mode instances --instances "%INSTANCES_CFG%" --debug >> "%LOG%" 2>&1
set "EC=%ERRORLEVEL%"

echo [%date% %time%] ==== exited with code %EC% ==== >> "%LOG%"
if "%EC%"=="0" (
    echo [INFO] Finished (instances mode; use second Ctrl+C to stop).
) else (
    echo [WARN] Exited with code %EC%. Check log: %LOG%
)
exit /b %EC%