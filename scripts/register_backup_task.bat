@echo off
set SCRIPT_DIR=%~dp0
set PYTHON=C:\Users\Work\anaconda3\python.exe
schtasks /Create /TN "AppyHour Weekly Offsite Backup" /SC WEEKLY /D SUN /ST 02:00 /TR "\"%PYTHON%\" \"%SCRIPT_DIR%backup_offsite.py\"" /F
