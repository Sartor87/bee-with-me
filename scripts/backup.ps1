<#
.SYNOPSIS
    Dumps the Bee With Me database to a timestamped file.

    Run it after every operation, and on a schedule between them. The whole record of a
    callout lives in one Docker volume on one laptop; this is the only thing standing
    between a disk failure and losing it.

    Works with Podman (default) or Docker; set CONTAINER_ENGINE to force one. Dumps are
    pg_dump custom format (.dump); restore with scripts\restore.ps1, which runs pg_restore (see the hint printed at the end).

.PARAMETER OutDir
    Where to write the dump. Point this at a USB stick or a second drive — a backup on
    the same disk as the database is not a backup.

.PARAMETER Keep
    How many dumps to retain in OutDir (oldest are pruned). Default 30.

.PARAMETER Container
    Dump this container instead of the one found by its compose labels (project bee-with-me,
    service db). The start scripts use it to back up the database of a 1.7.1-or-earlier install
    (older compose project name) before they stop it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1 -OutDir E:\bee-backups
#>

param(
    [string]$OutDir = (Join-Path $env:USERPROFILE 'Desktop\bee-backups'),
    [int]$Keep = 30,
    [string]$Container
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

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    # Dumps hold every position and name of a callout: a folder we create is for this user only
    # (no inherited ACEs; the dumps inside inherit this). An existing folder is left as it is.
    icacls "$OutDir" /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not restrict access to $OutDir (icacls failed)" }
}
$OutDir = (Resolve-Path $OutDir).Path   # .NET file calls below don't follow PowerShell's location

$stamp  = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$target = Join-Path $OutDir "beewithme_$stamp.dump"

# Find the db container by its compose labels (project + service): works for podman and docker
# compose alike, and never picks another compose project's `db` service.
# -Container overrides the lookup (upgrade from an older install, see start.ps1).
$container = if ($Container) { $Container }
             else { & $engine ps -q --filter 'label=com.docker.compose.project=bee-with-me' --filter 'label=com.docker.compose.service=db' | Select-Object -First 1 }
if (-not $container) {
    throw "Database container is not running - start it with: $engine compose -p bee-with-me -f docker\docker-compose.yaml up -d"
}

# Custom format (-Fc) is binary and compressed: write it inside the container, then copy it out.
# Piping it through PowerShell would re-encode the bytes and corrupt the dump.
$inContainer = "/tmp/beewithme_$stamp.dump"

# State for the backup marker (last-backup.json, see below), read just before the dump: which server
# this is and which migrations it has. The backend migrates only after a backup of this exact state.
$createdAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$systemId = (& $engine exec $container psql -U $user -d $db -Atc 'SELECT system_identifier FROM pg_control_system()' | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $systemId -notmatch '^\d+$') { throw "Could not read the database's system_identifier ($systemId)" }
$hasMigrations = (& $engine exec $container psql -U $user -d $db -Atc "SELECT to_regclass('public.schema_migrations') IS NOT NULL" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not read the migration state - check the container logs' }
$applied = @()
if ($hasMigrations -eq 't') {
    $applied = @(& $engine exec $container psql -U $user -d $db -Atc 'SELECT version FROM schema_migrations ORDER BY version' |
                 ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0) { throw 'Could not read schema_migrations - check the container logs' }
}

Write-Host "==> Dumping $db to $target ($engine)" -ForegroundColor Cyan
try {
    & $engine exec $container pg_dump -Fc -U $user -d $db -f $inContainer
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed - check the container logs' }
    & $engine cp "${container}:$inContainer" $target
    if ($LASTEXITCODE -ne 0) { throw "$engine cp failed" }
} finally {
    # Always remove the temp dump inside the container, also when the dump or the copy failed.
    & $engine exec $container rm -f $inContainer | Out-Null
}

if ((Get-Item $target).Length -eq 0) { throw 'Dump is empty - check the container logs' }
$size = [math]::Round((Get-Item $target).Length / 1MB, 2)
Write-Host "==> Wrote $size MB" -ForegroundColor Green

# Backup marker next to the dump: the backend applies pending migrations only when the marker in
# data/backups (BACKUP_MARKER_PATH) is of this server, in its current state, and under 24 h old.
$marker = Join-Path $OutDir 'last-backup.json'
$markerJson = [ordered]@{
    system_identifier = $systemId
    applied           = $applied
    dump              = (Split-Path $target -Leaf)
    created_at        = $createdAt
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText("$marker.tmp", $markerJson)   # UTF-8 without BOM
Move-Item -LiteralPath "$marker.tmp" -Destination $marker -Force
Write-Host "==> Marker $marker" -ForegroundColor Green

# Prune old dumps (custom-format .dump; older plain .sql dumps are left alone)
Get-ChildItem $OutDir -Filter 'beewithme_*.dump' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep |
    ForEach-Object { Write-Host "    pruning $($_.Name)"; Remove-Item $_.FullName }

Write-Host ''
Write-Host 'To restore (replaces the whole database; stop the backend first):' -ForegroundColor Gray
Write-Host "  powershell -ExecutionPolicy Bypass -File '$root\scripts\restore.ps1' '$target'" -ForegroundColor Gray
