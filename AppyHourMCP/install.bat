@echo off
echo ============================================
echo  AppyHour MCP Server - Installation
echo ============================================
echo.

echo [1/2] Installing Python dependencies...
pip install mcp[cli] pydantic requests openpyxl pyyaml
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: pip install failed. Make sure Python is on your PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Dependencies installed successfully!
echo.
echo ============================================
echo  NEXT STEPS
echo ============================================
echo.
echo 1. Open your Claude Desktop config file:
echo    %%APPDATA%%\Claude\claude_desktop_config.json
echo.
echo 2. Add this server entry inside "mcpServers":
echo.
echo    "appyhour": {
echo      "command": "python",
echo      "args": ["C:/Users/Work/AppyHour/AppyHourMCP/server.py"],
echo      "env": {
echo        "PYTHONPATH": "C:/Users/Work/AppyHour/GelPackCalculator;C:/Users/Work/AppyHour/InventoryReorder;C:/Users/Work/AppyHour/ShippingReports"
echo      }
echo    }
echo.
echo 3. Restart Claude Desktop.
echo.
pause
