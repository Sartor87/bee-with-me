<#
.SYNOPSIS
    Restores a Bee With Me database dump made by scripts\backup.ps1 or scripts/backup.sh.

    REPLACES the whole database with the dump: the database is dropped, created empty and the
    dump restored in a single transaction that stops at the first error, so nothing created after
    the backup (tables, schema_migrations rows) survives. Prints the migration status at the end.

    Stop the backend first: the script refuses while something listens on port 8000.
    Works with Podman (default) or Docker; set CONTAINER_ENGINE to force one.

.PARAMETER Dump
    The .dump file to restore.

.PARAMETER Yes
    Don't ask for confirmation.

.PARAMETER Force
    Restore even though something listens on port 8000 (the backend loses its connections).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\restore.ps1 'E:\bee-backups\beewithme_2026-10-02_101500.dump'
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Dump,
    [switch]$Yes,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path -LiteralPath $Dump -PathType Leaf) -or (Get-Item -LiteralPath $Dump).Length -eq 0) {
    throw "Dump file not found or empty: $Dump"
}
$Dump = (Resolve-Path -LiteralPath $Dump).Path

# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' as soon
# as stderr is redirected. Native calls run through this with 'Continue' (local to the function); the
# script decides on $LASTEXITCODE.
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

# Container engine: Podman first, Docker as the alternative. Override with $env:CONTAINER_ENGINE.
$engine = if ($env:CONTAINER_ENGINE) { $env:CONTAINER_ENGINE }
          elseif (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' }
          elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'docker' }
          else { throw 'Neither podman nor docker was found on PATH.' }

# Read DB settings out of .env so this never drifts from the running config
$envVars = Read-DotEnv (Join-Path $root '.env')
$db   = if ($envVars['POSTGRES_DB'])   { $envVars['POSTGRES_DB'] }   else { 'rescuer_locator' }
$user = if ($envVars['POSTGRES_USER']) { $envVars['POSTGRES_USER'] } else { 'rescuer' }

# Find the db container by its compose labels (project + service), like the backup script.
$container = Invoke-Native { & $engine ps -q --filter 'label=com.docker.compose.project=bee-with-me' --filter 'label=com.docker.compose.service=db' } | Select-Object -First 1
if (-not $container) {
    throw "Database container is not running - start it with: $engine compose -p bee-with-me -f docker\docker-compose.yaml up -d"
}

# The backend must not write into the database while it is replaced.
function Test-LocalPort([int]$port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { return ($client.ConnectAsync('127.0.0.1', $port).Wait(1000) -and $client.Connected) }
    catch { return $false }
    finally { $client.Dispose() }
}
if (Test-LocalPort 8000) {
    if ($Force) {
        Write-Host 'WARNING: something is listening on port 8000 (the backend?) - restoring anyway (-Force).' -ForegroundColor Yellow
    } else {
        throw 'Something is listening on port 8000 - stop the backend first (close its window), or pass -Force.'
    }
}

if (-not $Yes) {
    Write-Host "This REPLACES database '$db' in container $container with $Dump." -ForegroundColor Yellow
    $answer = Read-Host 'Type yes to continue'
    if ($answer -ne 'yes') { throw 'Not restored.' }
}

$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
$inContainer = "/tmp/beewithme_restore_$(Get-Date -Format 'yyyyMMddHHmmss')_$suffix.dump"
try {
    Invoke-Native { & $engine cp $Dump "${container}:$inContainer" }
    if ($LASTEXITCODE -ne 0) { throw "$engine cp failed" }

    Write-Host "==> Replacing $db ($engine)" -ForegroundColor Cyan
    Invoke-Native { & $engine exec $container dropdb --if-exists --force -U $user $db }
    if ($LASTEXITCODE -ne 0) { throw 'dropdb failed - the database was not changed' }
    Invoke-Native { & $engine exec $container createdb -U $user -O $user $db }
    if ($LASTEXITCODE -ne 0) { throw "createdb failed - database '$db' does not exist now; run the restore again" }
    Invoke-Native { & $engine exec $container pg_restore --exit-on-error --single-transaction --no-owner -U $user -d $db $inContainer }
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore failed - nothing was restored and database '$db' is now EMPTY. Fix the cause and run the restore again with the same dump."
    }
} finally {
    # Always remove the temp dump inside the container, also when a step failed.
    Invoke-Native { & $engine exec $container rm -f $inContainer } | Out-Null
}
Write-Host "==> Restored $Dump" -ForegroundColor Green

# Show where the restored database stands (pending migrations are applied by the next start).
$py = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $py) {
    Write-Host '==> python -m backend.db.migrate status' -ForegroundColor Cyan
    Push-Location $root
    try { & $py -m backend.db.migrate status } finally { Pop-Location }
    Write-Host "    (exit $($LASTEXITCODE): 0 up to date, 10 pending - applied by the next start, 2 database newer than the app)" -ForegroundColor Gray
} else {
    Write-Host 'No .venv found - check the state later with: python -m backend.db.migrate status'
}
