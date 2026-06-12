@echo off
REM Wednesday afternoon ops run: sync Gorgias + generate T+8 cohort report.
REM Scheduled for Wed 2pm via Task Scheduler. Single owner of the weekly update.

setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON=C:\Users\Work\anaconda3\python.exe"
set "LOG_DIR=%SCRIPT_DIR%logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "STAMP=%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%_%TIME:~0,2%-%TIME:~3,2%"
set "STAMP=%STAMP: =0%"
set "LOG=%LOG_DIR%\wednesday_ops_%STAMP%.log"

echo [%DATE% %TIME%] Starting wednesday_ops_run %*> "%LOG%"
"%PYTHON%" "%SCRIPT_DIR%wednesday_ops_run.py" %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] Exit code: %RC% >> "%LOG%"

type "%LOG%"
exit /b %RC%
