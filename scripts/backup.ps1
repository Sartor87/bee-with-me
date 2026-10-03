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

# Windows PowerShell 5.1 turns a native command's stderr into a terminating error under 'Stop' as soon
# as stderr is redirected (a warning from podman with exit code 0 would abort the backup). Native calls
# run through this with 'Continue' (local to the function); the script decides on $LASTEXITCODE.
function Invoke-Native([scriptblock]$Command) {
    $ErrorActionPreference = 'Continue'
    & $Command
}

# .env values: surrounding quotes, a trailing CR and an inline " # comment" are not part of the value;
# a leading `export ` (shell-style .env) is accepted.
function Read-DotEnv([string]$Path) {
    $vars = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $vars }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
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
# A process environment variable wins over the file (the same precedence as the backend's settings).
$db   = if ($env:POSTGRES_DB)   { $env:POSTGRES_DB }   elseif ($envVars['POSTGRES_DB'])   { $envVars['POSTGRES_DB'] }   else { 'rescuer_locator' }
$user = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } elseif ($envVars['POSTGRES_USER']) { $envVars['POSTGRES_USER'] } else { 'rescuer' }

# Dumps hold every position and name of a callout: the dump folder is for this user only (no inherited
# ACEs; the dumps inside inherit this). Granted by SID, which also works for domain and renamed accounts.
$mySid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
function Set-PrivateAcl([string]$Dir) {
    icacls "$Dir" /inheritance:r /grant:r "*${mySid}:(OI)(CI)F" | Out-Null
    return ($LASTEXITCODE -eq 0)
}
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    if (-not (Set-PrivateAcl $OutDir)) { throw "Could not restrict access to $OutDir (icacls failed)" }
} else {
    # An existing folder that still inherits its permissions is restricted too, but only when it holds
    # nothing but dumps (a folder of ours); anything else is left as it is, with a warning.
    $protected = try { (Get-Acl -LiteralPath $OutDir).AreAccessRulesProtected } catch { $false }
    if (-not $protected) {
        $other = @(Get-ChildItem -LiteralPath $OutDir -Force |
                   Where-Object { $_.Name -notlike 'beewithme_*' -and $_.Name -notlike 'last-backup.json*' })
        if ($other.Count -gt 0 -or -not (Set-PrivateAcl $OutDir)) {
            Write-Host ("WARNING: $OutDir is not restricted to this user (it holds other files, or the drive has no " +
                        'ACLs, e.g. FAT/exFAT). The dumps in it hold personal data: keep the folder private.') -ForegroundColor Yellow
        }
    }
}
$OutDir = (Resolve-Path $OutDir).Path   # .NET file calls below don't follow PowerShell's location

# Random suffix: two runs in the same second never share a file name (here or in the container).
$stamp   = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$suffix  = [guid]::NewGuid().ToString('N').Substring(0, 6)
$target  = Join-Path $OutDir "beewithme_${stamp}_$suffix.dump"
$partial = "$target.partial"   # becomes $target only after it was validated

# Find the db container by its compose labels (project + service): works for podman and docker
# compose alike, and never picks another compose project's `db` service.
# -Container overrides the lookup (upgrade from an older install, see start.ps1).
$container = if ($Container) { $Container }
             else { Invoke-Native { & $engine ps -q --filter 'label=com.docker.compose.project=bee-with-me' --filter 'label=com.docker.compose.service=db' } | Select-Object -First 1 }
if (-not $container) {
    throw "Database container is not running - start it with: $engine compose -p bee-with-me -f docker\docker-compose.yaml up -d"
}

# Custom format (-Fc) is binary and compressed: write it inside the container, then copy it out.
# Piping it through PowerShell would re-encode the bytes and corrupt the dump.
$inContainer = "/tmp/beewithme_${stamp}_$suffix.dump"

# State for the backup marker (last-backup.json, see below), read just before the dump: which server
# this is and which migrations it has. The backend migrates only after a backup of this exact state.
$createdAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$systemId = (Invoke-Native { & $engine exec $container psql -U $user -d $db -Atc 'SELECT system_identifier FROM pg_control_system()' } | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $systemId -notmatch '^\d+$') { throw "Could not read the database's system_identifier ($systemId)" }
$database = (Invoke-Native { & $engine exec $container psql -U $user -d $db -Atc 'SELECT current_database()' } | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $database) { throw "Could not read the database name ($database)" }
$hasMigrations = (Invoke-Native { & $engine exec $container psql -U $user -d $db -Atc "SELECT to_regclass('public.schema_migrations') IS NOT NULL" } | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not read the migration state - check the container logs' }
$applied = @()
if ($hasMigrations -eq 't') {
    $applied = @(Invoke-Native { & $engine exec $container psql -U $user -d $db -Atc 'SELECT version FROM schema_migrations ORDER BY version' } |
                 ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    if ($LASTEXITCODE -ne 0) { throw 'Could not read schema_migrations - check the container logs' }
}

Write-Host "==> Dumping $db to $target ($engine)" -ForegroundColor Cyan
try {
    Invoke-Native { & $engine exec $container pg_dump -Fc -U $user -d $db -f $inContainer }
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed - check the container logs' }
    # A dump pg_restore cannot list is no backup: check it before it counts as one.
    Invoke-Native { & $engine exec $container pg_restore -l $inContainer } | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore -l cannot read the dump - not kept as a backup' }
    $expected = (Invoke-Native { & $engine exec $container stat -c %s $inContainer } | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $expected -notmatch '^\d+$') { throw 'Could not read the dump size in the container' }
    Invoke-Native { & $engine cp "${container}:$inContainer" $partial }
    if ($LASTEXITCODE -ne 0) { throw "$engine cp failed" }
    if ((Get-Item -LiteralPath $partial).Length -ne [int64]$expected) { throw "$engine cp copied an incomplete dump" }
    Move-Item -LiteralPath $partial -Destination $target
} catch {
    # A partial dump is never left behind looking like a backup.
    Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    throw
} finally {
    # Always remove the temp dump inside the container, also when the dump or the copy failed.
    Invoke-Native { & $engine exec $container rm -f $inContainer } | Out-Null
}

if ((Get-Item $target).Length -eq 0) { throw 'Dump is empty - check the container logs' }
$size = [math]::Round((Get-Item $target).Length / 1MB, 2)
Write-Host "==> Wrote $size MB" -ForegroundColor Green

# Backup marker next to the dump: the backend applies pending migrations only when the marker in
# data/backups (BACKUP_MARKER_PATH) is of this server and database, in its current state, under 24 h
# old, and its dump is there.
$marker = Join-Path $OutDir 'last-backup.json'
$markerJson = [ordered]@{
    system_identifier = $systemId
    database          = $database
    applied           = $applied
    dump              = (Split-Path $target -Leaf)
    created_at        = $createdAt
} | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText("$marker.tmp", $markerJson)   # UTF-8 without BOM
Move-Item -LiteralPath "$marker.tmp" -Destination $marker -Force
Write-Host "==> Marker $marker" -ForegroundColor Green

# Prune old dumps (custom-format .dump only: .dump.partial and older plain .sql dumps are left alone)
Get-ChildItem -LiteralPath $OutDir -Filter 'beewithme_*.dump' |
    Where-Object { $_.Name -like 'beewithme_*.dump' } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep |
    ForEach-Object { Write-Host "    pruning $($_.Name)"; Remove-Item -LiteralPath $_.FullName }

Write-Host ''
Write-Host 'To restore (replaces the whole database; stop the backend first):' -ForegroundColor Gray
Write-Host "  powershell -ExecutionPolicy Bypass -File ""$root\scripts\restore.ps1"" ""$target""" -ForegroundColor Gray
