@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Qixing Gaozhao ETF Rotation - SIM (paper) trading launcher
REM  Built-in auto-exit at 15:05 (provided by main.py sim mode)
REM  Usage: double-click, or trigger via Windows Scheduled Task
REM ============================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM ---------------- Config (edit if needed) ----------------
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "QMT_PATH=D:\国金QMT交易端模拟New\userdata_mini"
set "STRATEGY=qixing_gaozhao"
set "ACCOUNT="                REM leave empty = auto-detect first account

REM ---------------- Logging ----------------
set "LOGDIR=%ROOT%LOG"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "DS=%date:~0,4%%date:~5,2%%date:~8,2%"
set "LOG=%LOGDIR%\sim_%STRATEGY%_%DS%.log"

echo [%date% %time%] ==== QixingGaozhao SIM launcher start ==== >> "%LOG%"

REM ---------------- Pre-flight checks ----------------
if not exist "%PYTHON%" (
    echo [ERROR] venv python not found: %PYTHON%
    echo [ERROR] venv python not found: %PYTHON% >> "%LOG%"
    exit /b 1
)
if not exist "%QMT_PATH%" (
    echo [ERROR] QMT userdata_mini not found: %QMT_PATH%
    echo [ERROR] QMT userdata_mini not found: %QMT_PATH% >> "%LOG%"
    exit /b 1
)
if not exist "%ROOT%main.py" (
    echo [ERROR] main.py not found: %ROOT%main.py
    echo [ERROR] main.py not found: %ROOT%main.py >> "%LOG%"
    exit /b 1
)

REM ---------------- Launch ----------------
echo [INFO] Log file: %LOG%
echo [INFO] Launching SIM trading (auto-exit at 15:05)...

if "%ACCOUNT%"=="" (
    "%PYTHON%" -u main.py --mode sim --strategy %STRATEGY% --qmt-path "%QMT_PATH%" --debug >> "%LOG%" 2>&1
) else (
    "%PYTHON%" -u main.py --mode sim --strategy %STRATEGY% --qmt-path "%QMT_PATH%" --account %ACCOUNT% --debug >> "%LOG%" 2>&1
)
set "EC=%ERRORLEVEL%"

echo [%date% %time%] ==== exited with code %EC% ==== >> "%LOG%"
if "%EC%"=="0" (
    echo [INFO] Finished normally (15:05 auto-exit).
) else (
    echo [WARN] Exited with code %EC%. Check log: %LOG%
)
exit /b %EC%
