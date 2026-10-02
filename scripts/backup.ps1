<#
.SYNOPSIS
    Dumps the Bee With Me database to a timestamped file.

    Run it after every operation, and on a schedule between them. The whole record of a
    callout lives in one Docker volume on one laptop; this is the only thing standing
    between a disk failure and losing it.

    Works with Podman (default) or Docker; set CONTAINER_ENGINE to force one. Dumps are
    pg_dump custom format (.dump); restore with pg_restore (see the hint printed at the end).

.PARAMETER OutDir
    Where to write the dump. Point this at a USB stick or a second drive — a backup on
    the same disk as the database is not a backup.

.PARAMETER Keep
    How many dumps to retain in OutDir (oldest are pruned). Default 30.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1 -OutDir E:\bee-backups
#>

param(
    [string]$OutDir = (Join-Path $env:USERPROFILE 'Desktop\bee-backups'),
    [int]$Keep = 30
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

# Container engine: Podman first, Docker as the alternative. Override with $env:CONTAINER_ENGINE.
$engine = if ($env:CONTAINER_ENGINE) { $env:CONTAINER_ENGINE }
          elseif (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' }
          elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'docker' }
          else { throw 'Neither podman nor docker was found on PATH.' }

# Read DB settings out of .env so this never drifts from the running config
$envVars = @{}
foreach ($line in Get-Content (Join-Path $root '.env')) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') { $envVars[$matches[1]] = $matches[2] }
}
$db   = if ($envVars['POSTGRES_DB'])   { $envVars['POSTGRES_DB'] }   else { 'rescuer_locator' }
$user = if ($envVars['POSTGRES_USER']) { $envVars['POSTGRES_USER'] } else { 'rescuer' }

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$stamp  = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$target = Join-Path $OutDir "beewithme_$stamp.dump"

# Find the db container by its compose labels (project + service): works for podman and docker
# compose alike, and never picks another compose project's `db` service.
$container = & $engine ps -q --filter 'label=com.docker.compose.project=bee-with-me' --filter 'label=com.docker.compose.service=db' | Select-Object -First 1
if (-not $container) {
    throw "Database container is not running - start it with: $engine compose -f docker\docker-compose.yaml up -d"
}

# Custom format (-Fc) is binary and compressed: write it inside the container, then copy it out.
# Piping it through PowerShell would re-encode the bytes and corrupt the dump.
$inContainer = "/tmp/beewithme_$stamp.dump"
Write-Host "==> Dumping $db to $target ($engine)" -ForegroundColor Cyan
& $engine exec $container pg_dump -Fc -U $user -d $db -f $inContainer
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed - check the container logs' }
& $engine cp "${container}:$inContainer" $target
if ($LASTEXITCODE -ne 0) { throw "$engine cp failed" }
& $engine exec $container rm -f $inContainer | Out-Null

if ((Get-Item $target).Length -eq 0) { throw 'Dump is empty - check the container logs' }
$size = [math]::Round((Get-Item $target).Length / 1MB, 2)
Write-Host "==> Wrote $size MB" -ForegroundColor Green

# Prune old dumps (custom-format .dump; older plain .sql dumps are left alone)
Get-ChildItem $OutDir -Filter 'beewithme_*.dump' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep |
    ForEach-Object { Write-Host "    pruning $($_.Name)"; Remove-Item $_.FullName }

Write-Host ''
Write-Host 'To restore (replaces the current data):' -ForegroundColor Gray
Write-Host "  $engine cp '$target' ${container}:/tmp/restore.dump" -ForegroundColor Gray
Write-Host "  $engine exec $container pg_restore --clean --if-exists -U $user -d $db /tmp/restore.dump" -ForegroundColor Gray
