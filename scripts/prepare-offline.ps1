<#
.SYNOPSIS
    Prepares this laptop to start Bee With Me without internet (B62). Run it at home, with internet,
    after installing or updating the app.

.DESCRIPTION
    1. Downloads every backend package (and its dependencies) for this laptop's Python into vendor\wheels.
       start.ps1 installs from there when a package is missing and there is no internet.
    2. Installs the backend packages into .venv from that folder.
    3. Installs the frontend packages (npm ci), which also fills the local npm cache.

    vendor\ is not committed to git. Run again after every update that changes backend\requirements.txt
    or frontend\package-lock.json.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\prepare-offline.ps1
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = "$root\.venv\Scripts\python.exe"
$wheels = "$root\vendor\wheels"

if (-not (Test-Path -LiteralPath $python)) {
    $py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' }
          elseif (Get-Command py -ErrorAction SilentlyContinue) { 'py' }
          else { throw 'Python was not found on PATH (tried "python" and "py"). Install Python 3.11+ from python.org and re-run.' }
    Write-Host '==> Creating Python virtual environment (.venv)' -ForegroundColor Cyan
    & $py -m venv "$root\.venv"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
}

Write-Host "==> Downloading backend packages into $wheels" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $wheels | Out-Null
& $python -m pip download -r "$root\backend\requirements.txt" -d $wheels
if ($LASTEXITCODE -ne 0) { throw 'pip download failed - is the internet up?' }

Write-Host '==> Installing backend packages from vendor\wheels' -ForegroundColor Cyan
& $python -m pip install --no-index --find-links $wheels -r "$root\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Installing from vendor\wheels failed - see the messages above.' }

Write-Host '==> Installing frontend packages (npm ci)' -ForegroundColor Cyan
Push-Location "$root\frontend"
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed - is the internet up?' }
} finally { Pop-Location }

Write-Host 'Done. This laptop can now start Bee With Me without internet.' -ForegroundColor Green
