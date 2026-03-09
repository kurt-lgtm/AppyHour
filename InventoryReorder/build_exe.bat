@echo off
setlocal

echo ============================================
echo   Inventory Reorder System - Build Script
echo ============================================
echo.

REM --- Auto-detect Anaconda Python ---
if exist "%USERPROFILE%\anaconda3\python.exe" (
    set PYTHON_CMD=%USERPROFILE%\anaconda3\python.exe
    set PIP_CMD=%USERPROFILE%\anaconda3\Scripts\pip.exe
    goto :found
)
if exist "%LOCALAPPDATA%\anaconda3\python.exe" (
    set PYTHON_CMD=%LOCALAPPDATA%\anaconda3\python.exe
    set PIP_CMD=%LOCALAPPDATA%\anaconda3\Scripts\pip.exe
    goto :found
)

REM --- Fallback to system Python ---
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
    set PIP_CMD=pip
    goto :found
)

echo ERROR: Python not found. Install Anaconda or add Python to PATH.
pause
exit /b 1

:found
echo Using Python: %PYTHON_CMD%
echo.

echo [1/3] Installing dependencies...
%PIP_CMD% install requests openpyxl pyinstaller --quiet
echo.

echo [2/3] Building executable...
%PYTHON_CMD% -m PyInstaller --onefile --windowed --name "InventoryReorder" --clean inventory_reorder.py
echo.

echo [3/3] Verifying build...
if exist "dist\InventoryReorder.exe" (
    echo.
    echo ============================================
    echo   BUILD SUCCESSFUL!
    echo   Executable: dist\InventoryReorder.exe
    echo ============================================
) else (
    echo.
    echo BUILD FAILED. Check errors above.
)

echo.
pause
