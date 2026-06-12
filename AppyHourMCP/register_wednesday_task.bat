@echo off
REM Register Wednesday 2pm ops run with Windows Task Scheduler.
REM Run this ONCE to install the schedule. Requires admin only if you
REM want highest privileges — omit /RL HIGHEST for normal user run.

setlocal
set "TASK_NAME=AppyHour Wednesday Ops Run"
set "BAT=C:\Users\Work\Claude Projects\AppyHour\AppyHourMCP\wednesday_ops_run.bat"

echo Registering scheduled task: %TASK_NAME%
echo Runs: Weekly, Wednesday 14:00 local time
echo Target: %BAT%
echo.

schtasks /Create ^
  /TN "%TASK_NAME%" ^
  /TR "\"%BAT%\"" ^
  /SC WEEKLY ^
  /D WED ^
  /ST 14:00 ^
  /F

if %ERRORLEVEL% EQU 0 (
  echo.
  echo Task registered successfully.
  echo   View:   schtasks /Query /TN "%TASK_NAME%" /V /FO LIST
  echo   Run now: schtasks /Run /TN "%TASK_NAME%"
  echo   Delete: schtasks /Delete /TN "%TASK_NAME%" /F
) else (
  echo.
  echo Task registration failed. Error code: %ERRORLEVEL%
)
