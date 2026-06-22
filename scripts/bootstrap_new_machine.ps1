<#
.SYNOPSIS
  Install every tool AppyHour needs on a fresh Windows machine.

.DESCRIPTION
  Run this in an elevated PowerShell on the NEW machine after copying the
  repo over (or `git clone`). Installs system tools via winget, then the
  Python + npm packages the platform depends on. Idempotent — re-running
  skips already-installed packages.

  Companion to RESTORE.md. After this finishes, run:
      python scripts\restore_check.py

.NOTES
  - Requires winget (App Installer; preinstalled on Win10 21H2+ / Win11).
  - .NET Framework 4.8 ships with Win10/11 — pywebview's `netfx` backend
    needs Framework, NOT .NET 8/coreclr. We verify it's present, not install.
  - Run from the AppyHour repo root so the pip editable install resolves.
#>

[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\.."),
    [switch]$SkipPip,
    [switch]$SkipNpm
)

$ErrorActionPreference = 'Stop'

function Install-WinGet($id, $name) {
    Write-Host "==> $name ($id)" -ForegroundColor Cyan
    $installed = winget list --id $id --exact 2>$null | Select-String $id
    if ($installed) {
        Write-Host "    already installed, skipping." -ForegroundColor DarkGray
        return
    }
    winget install --id $id --exact --accept-source-agreements --accept-package-agreements -h
}

Write-Host "AppyHour new-machine bootstrap" -ForegroundColor Green
Write-Host "Repo root: $RepoRoot`n"

# 1. System tools ----------------------------------------------------------
Install-WinGet 'Anaconda.Anaconda3'  'Anaconda (Python 3.x + Tcl/Tk for tkinter)'
Install-WinGet 'Git.Git'             'Git'
Install-WinGet 'GitHub.cli'          'GitHub CLI (gh)'
Install-WinGet 'OpenJS.NodeJS.LTS'   'Node.js LTS (npm)'
Install-WinGet 'Anthropic.Claude'    'Claude Desktop'

# 2. .NET Framework check (required for pywebview netfx backend) -----------
Write-Host "==> .NET Framework 4.8 check" -ForegroundColor Cyan
$ndp = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue
if ($ndp -and $ndp.Release -ge 528040) {
    Write-Host "    .NET Framework 4.8+ present (Release=$($ndp.Release))." -ForegroundColor DarkGray
} else {
    Write-Warning "    .NET Framework 4.8 NOT detected. Install it (Kori desktop needs the netfx backend, NOT .NET 8)."
    Write-Warning "    winget install Microsoft.DotNet.Framework.DeveloperPack_4_8   (or Windows Update)"
}

# Refresh PATH so freshly-installed git/node/python resolve in this session
$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path','User')

# 3. npm global packages ---------------------------------------------------
# NOTE: Claude Code is NOT installed here — it's a native binary, install it
# separately and FIRST (it's how you'd run this script via a local agent):
#     irm https://claude.ai/install.ps1 | iex
# Node is needed ONLY for the Shopify dev-mcp server below.
if (-not $SkipNpm) {
    Write-Host "`n==> npm global packages (Shopify dev-mcp)" -ForegroundColor Cyan
    npm install -g @shopify/dev-mcp            # shopify-dev MCP server (.mcp.json)
}

# 4. Python packages (editable install of this repo) -----------------------
if (-not $SkipPip) {
    Write-Host "`n==> pip install -e .[dev,fulfillment,shipping,mcp]" -ForegroundColor Cyan
    Push-Location $RepoRoot
    try {
        python -m pip install --upgrade pip
        python -m pip install -e ".[dev,fulfillment,shipping,mcp]"
    } finally {
        Pop-Location
    }
}

Write-Host "`nBootstrap complete." -ForegroundColor Green
Write-Host "Next:" -ForegroundColor Green
Write-Host "  1. Copy %APPDATA%\AppyHour\ + ~/.knowledge\ + ~/.claude\ from the old SSD (RESTORE.md 0a)"
Write-Host "  2. gh auth login"
Write-Host "  3. python scripts\restore_check.py     # see what's still missing"
Write-Host "  4. python scripts\restore_check.py --grep-paths   # if username != 'Work'"
