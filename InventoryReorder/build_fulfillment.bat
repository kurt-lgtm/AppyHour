@echo off
setlocal

echo ============================================
echo   Fulfillment Planner - Build Script
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
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
    set PIP_CMD=pip
    goto :found
)
echo ERROR: Python not found.
pause
exit /b 1

:found
echo Using Python: %PYTHON_CMD%
echo.

echo [1/4] Installing dependencies...
%PIP_CMD% install flask openpyxl requests pywebview fpdf2 pyinstaller --quiet
echo.

echo [2/4] Cleaning previous build...
if exist "build\FulfillmentPlanner" rmdir /s /q "build\FulfillmentPlanner"
echo.

echo [3/4] Building executable...
%PYTHON_CMD% -m PyInstaller FulfillmentPlanner.spec --clean --noconfirm
echo.

echo [4/4] Verifying build...
if exist "dist\FulfillmentPlanner.exe" (
    echo.
    echo ============================================
    echo   BUILD SUCCESSFUL!
    echo   Executable: dist\FulfillmentPlanner.exe
    echo ============================================
    echo.
    echo   Place next to inventory_reorder_settings.json
    echo   Double-click to launch (opens in browser)
    echo.
) else (
    echo.
    echo BUILD FAILED. Check errors above.
)

pause
