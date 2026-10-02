<#
.SYNOPSIS
    Starts the whole Bee With Me stack on Windows: Postgres/PostGIS (Podman or Docker), the
    FastAPI backend, and the Vue frontend - instead of starting each one by hand.

    This script lives in the project root and uses its own location to find the
    project, so it works wherever the folder is copied to.

.PARAMETER ProjectPath
    Path to the bee-with-me project folder. Defaults to the folder this script
    is in.

.PARAMETER SkipContainers
    Don't start the database container (use this if it is already running). -SkipDocker still works.

.PARAMETER NoBrowser
    Don't auto-open the frontend in the default browser once it's up.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start.ps1 -ProjectPath 'D:\bee-with-me'
#>

param(
    [string]$ProjectPath = $PSScriptRoot,
    [Alias('SkipDocker')]
    [switch]$SkipContainers,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host $msg -ForegroundColor Yellow }

# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' as soon
# as stderr is redirected (e.g. a podman warning on `info` with exit code 0 would abort the start).
# Native calls run through this with 'Continue' (local to the function); the script decides on $LASTEXITCODE.
function Invoke-Native([scriptblock]$Command) {
    $ErrorActionPreference = 'Continue'
    & $Command
}

# .env values: surrounding quotes, a trailing CR and an inline " # comment" are not part of the value.
function Read-DotEnv([string]$Path) {
    $vars = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $vars }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
        $key = $matches[1]
        $value = ($matches[2] -replace "`r$", '').Trim()
        if ($value -match '^"([^"]*)"') { $value = $matches[1] }
        elseif ($value -match "^'([^']*)'") { $value = $matches[1] }
        else { $value = ($value -replace '\s+#.*$', '').Trim() }
        $vars[$key] = $value
    }
    return $vars
}

if (-not (Test-Path $ProjectPath)) {
    throw "Project folder not found: $ProjectPath`nPass the real location with -ProjectPath, e.g.:`n  powershell -ExecutionPolicy Bypass -File .\start.ps1 -ProjectPath 'C:\path\to\bee-with-me'"
}
$root = (Resolve-Path $ProjectPath).Path
Set-Location $root
Write-Step "Using project folder: $root"

# -- .env --------------------------------------------------------------------
if (-not (Test-Path "$root\.env")) {
    Write-Step 'No .env found - copying .env.example'
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Warn 'Edit .env with real values (POSTGRES_PASSWORD, SECRET_KEY, HID_VENDOR_ID/HID_PRODUCT_ID, ...) before relying on this for anything but a quick test.'
}

# -- Database container (Podman first, Docker as the alternative) ----------------
$engine = if ($env:CONTAINER_ENGINE) { $env:CONTAINER_ENGINE }
          elseif (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' }
          elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'docker' }
          else { $null }

if (-not $SkipContainers) {
    if (-not $engine) {
        throw 'Neither podman nor docker was found on PATH. Install Podman (https://podman.io) or Docker, or re-run with -SkipContainers if the database is already running elsewhere.'
    }
    Invoke-Native { & $engine info *> $null }
    if ($LASTEXITCODE -ne 0) {
        if ($engine -eq 'podman') { throw 'Podman is installed but not running. Start it with: podman machine start' }
        throw 'Docker is installed but not running. Start Docker Desktop and re-run.'
    }

    # Podman on Windows runs in a WSL machine: named volume + host network for Postgres
    # (see docker\docker-compose.podman-machine.yaml for why).
    $composeFiles = @('-f', "$root\docker\docker-compose.yaml")
    if ($engine -eq 'podman') { $composeFiles += @('-f', "$root\docker\docker-compose.podman-machine.yaml") }

    # Upgrade from 1.7.1 or earlier: the stack ran as compose project "docker" (docker-db-1) on the
    # same port and, on the base file, the same data folder. Back that database up, then stop the old
    # project (no -v: data\pgdata stays and the new project reuses it), before the new one starts.
    $oldDb = Invoke-Native { & $engine ps -q --filter 'label=com.docker.compose.project=docker' --filter 'label=com.docker.compose.service=db' } | Select-Object -First 1
    $oldDump = $null
    if ($oldDb) {
        Write-Step "Found the database of an older install (compose project 'docker', container $oldDb): backing it up, then stopping it"
        try { & "$root\scripts\backup.ps1" -OutDir "$root\data\backups" -Container $oldDb }
        catch { throw 'Backup of the old database failed - not continuing; the old install is left running.' + " $($_.Exception.Message)" }
        $oldDump = Join-Path "$root\data\backups" ((Get-Content "$root\data\backups\last-backup.json" -Raw | ConvertFrom-Json).dump)
        Invoke-Native { & $engine compose -p docker -f "$root\docker\docker-compose.yaml" down }
        if ($LASTEXITCODE -ne 0) { throw "$engine compose -p docker down failed - stop the old containers (docker-db-1, docker-tiles-1) yourself, then re-run." }
    }

    Write-Step "Starting database ($engine compose -p bee-with-me up -d)"
    Invoke-Native { & $engine compose -p bee-with-me @composeFiles up -d }
    if ($LASTEXITCODE -ne 0) { throw "$engine compose -p bee-with-me up failed - see the output above." }

    Write-Step 'Waiting for Postgres to accept connections'
    $pgPort = (Read-DotEnv "$root\.env")['POSTGRES_PORT']
    if ($pgPort -notmatch '^\d+$') { $pgPort = '5432' }

    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    do {
        $ready = (Test-NetConnection -ComputerName 'localhost' -Port $pgPort -InformationLevel Quiet -WarningAction SilentlyContinue)
        if (-not $ready) { Start-Sleep -Seconds 1 }
    } until ($ready -or (Get-Date) -gt $deadline)

    if (-not $ready) {
        Write-Warn "Postgres didn't come up on port $pgPort within 60s - continuing anyway. Check: $engine compose -f docker\docker-compose.yaml -p bee-with-me logs"
    }

    if ($oldDump -and $engine -eq 'podman') {
        # With the podman-machine override the old data is in the volume docker_pgdata, not data\pgdata:
        # the new database (volume bee-with-me_pgdata) is empty. Don't start the backend on it.
        throw ("The old install kept its data in the Podman volume docker_pgdata; the new database " +
               "(volume bee-with-me_pgdata) starts empty, so the backend is not started. Restore the " +
               "backup just taken into it, then start again:`n" +
               "  powershell -ExecutionPolicy Bypass -File `"$root\scripts\restore.ps1`" `"$oldDump`"`n" +
               "  powershell -ExecutionPolicy Bypass -File `"$root\start.ps1`"`n" +
               "(The volume docker_pgdata is left as it was.)")
    }
}

# -- Python venv + backend deps -----------------------------------------------
$venvActivate = "$root\.venv\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
    Write-Step 'Creating Python virtual environment (.venv)'
    $py = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' }
          elseif (Get-Command py -ErrorAction SilentlyContinue) { 'py' }
          else { throw 'Python was not found on PATH (tried "python" and "py"). Install Python 3.11+ from python.org and re-run.' }
    & $py -m venv "$root\.venv"
    if (-not (Test-Path $venvActivate)) {
        throw "Failed to create the virtual environment at $root\.venv - if 'python' opened the Microsoft Store instead of actually running, that's the Windows app-execution-alias stub, not real Python. Install Python 3.11+ from https://python.org (check 'Add python.exe to PATH' during install), or disable the stub under Settings > Apps > Advanced app settings > App execution aliases, then re-run this script."
    }
}

Write-Step 'Installing/checking backend dependencies'
& "$root\.venv\Scripts\python.exe" -m pip install -q -r "$root\backend\requirements.txt"

# -- Database migrations: back up first if any are pending ---------------------
if (-not $SkipContainers) {
    Write-Step 'Checking database migrations'
    # Exit 3 means Postgres isn't accepting connections yet (slow after a reboot or
    # `podman machine start`): retry for up to 90 s rather than let the backend migrate later
    # without the backup this check exists for. Any other code is final at once.
    $migDeadline = (Get-Date).AddSeconds(90)
    while ($true) {
        & "$root\.venv\Scripts\python.exe" -m backend.db.migrate status
        $migExit = $LASTEXITCODE
        if ($migExit -ne 3 -or (Get-Date) -gt $migDeadline) { break }
        Write-Warn 'Database not reachable yet - retrying in 3 s'
        Start-Sleep -Seconds 3
    }
    switch ($migExit) {
        0  { }
        10 {
            Write-Step 'Migrations pending - taking a backup first (data\backups)'
            try { & "$root\scripts\backup.ps1" -OutDir "$root\data\backups" }
            catch { throw 'Backup failed - not starting, so the database is never migrated without a backup.' + " $($_.Exception.Message)" }
        }
        1  { throw 'Not starting: the migration files are invalid: see the message above.' }
        2  { throw 'The database is newer than this version of Bee With Me. Update the app (git pull) instead of starting an older one.' }
        default { throw "Could not check database migrations (database not reachable?) - not starting, so the database is never migrated without a backup. Check: $engine compose -f docker\docker-compose.yaml -p bee-with-me logs" }
    }
}

# -- Frontend deps -------------------------------------------------------------
if (-not (Test-Path "$root\frontend\node_modules")) {
    Write-Step 'Installing frontend dependencies (first run only - this can take a minute)'
    Push-Location "$root\frontend"
    npm install
    Pop-Location
}

# -- Backend (own window) ------------------------------------------------------
Write-Step 'Starting backend (uvicorn) in a new window'
Start-Process powershell -ArgumentList @(
    '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$root'; & '$venvActivate'; uvicorn backend.main:app"
) -WindowStyle Normal

# -- Frontend (own window) ------------------------------------------------------
Write-Step 'Starting frontend (vite) in a new window'
Start-Process powershell -ArgumentList @(
    '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$root\frontend'; npm run dev"
) -WindowStyle Normal

Start-Sleep -Seconds 2
if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }

Write-Host ''
Write-Host 'Bee With Me is starting up:' -ForegroundColor Green
Write-Host '  Backend:  http://localhost:8000  (API docs at /docs)'
Write-Host '  Frontend: http://localhost:5173'
Write-Host ''
Write-Host 'Backend and frontend run in their own windows - close a window (or Ctrl+C inside it) to stop that service.' -ForegroundColor Gray
$engineHint = if ($engine) { $engine } else { 'podman' }
Write-Host 'The database keeps running in its container until you stop it yourself:' -ForegroundColor Gray
Write-Host "  $engineHint compose -p bee-with-me -f docker\docker-compose.yaml down" -ForegroundColor Gray
