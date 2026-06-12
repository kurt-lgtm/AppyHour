@echo off
REM Gorgias sync+enrich — run from Warp or Task Scheduler
REM Usage:
REM   gorgias_update.bat                # last 7 days, live
REM   gorgias_update.bat --days 14      # last 14 days
REM   gorgias_update.bat --dry-run      # preview only

setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON=C:\Users\Work\anaconda3\python.exe"
set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "STAMP=%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%_%TIME:~0,2%-%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
set "LOG=%LOG_DIR%\gorgias_update_%STAMP%.log"

echo [%DATE% %TIME%] Starting gorgias_update %*> "%LOG%"
"%PYTHON%" "%SCRIPT_DIR%run_gorgias_update.py" %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] Exit code: %RC% >> "%LOG%"

type "%LOG%"
exit /b %RC%
