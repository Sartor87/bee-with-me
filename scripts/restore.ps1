<#
.SYNOPSIS
    Restores a Bee With Me database dump made by scripts\backup.ps1 or scripts/backup.sh.

    REPLACES the whole database with the dump. The dump is checked first (pg_restore -l), then
    restored into a side database <db>_restore_<suffix> in a single transaction that stops at the
    first error. Only when that worked is it swapped in: the current database is renamed to
    <db>_before_restore_<UTC stamp> (kept until you drop it) and the side database to <db>. Nothing
    created after the backup (tables, schema_migrations rows) survives; a failed restore leaves the
    current database untouched. Prints the migration status at the end.

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

# .env values: surrounding quotes, a trailing CR and an inline " # comment" are not part of the value;
# a leading `export ` (shell-style .env, lower case only, like python-dotenv) is accepted.
function Read-DotEnv([string]$Path) {
    $vars = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $vars }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -cnotmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { continue }
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

# The swap renames databases in SQL: plain lower-case names only, short enough for the
# _before_restore_<stamp> name to stay within PostgreSQL's 63-character limit.
if ($db -cnotmatch '^[a-z_][a-z0-9_]*\z' -or $db.Length -gt 33) {
    throw "POSTGRES_DB '$db': restore supports database names of up to 33 lower-case letters, digits and _ only."
}
if (@('postgres', 'template0', 'template1', 'template_postgis') -ccontains $db.ToLowerInvariant()) {
    throw "POSTGRES_DB '$db' is a system database - restore never replaces it. Set POSTGRES_DB to the app's database."
}
$restoreDb = "${db}_restore_$([guid]::NewGuid().ToString('N').Substring(0, 8))"

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

# First five bytes of a file as ASCII: 'PGDMP' for a pg_dump custom-format dump.
function Get-FileMagic([string]$Path) {
    $head = New-Object byte[] 5
    $fs = [System.IO.File]::OpenRead($Path)
    try { $read = $fs.Read($head, 0, 5) } finally { $fs.Dispose() }
    return [System.Text.Encoding]::ASCII.GetString($head, 0, $read)
}

$suffix = [guid]::NewGuid().ToString('N').Substring(0, 8)
$inContainer = "/tmp/beewithme_restore_$(Get-Date -Format 'yyyyMMddHHmmss')_$suffix.dump"
# The side database exists only between createdb and the swap; any failure in between drops it.
$restoreCreated = $false
$keptDb = $null
try {
    Invoke-Native { & $engine cp $Dump "${container}:$inContainer" }
    if ($LASTEXITCODE -ne 0) { throw "$engine cp failed" }

    # 1. Is this a dump pg_restore can read at all? Checked before any database is touched.
    Invoke-Native { & $engine exec $container pg_restore -l $inContainer } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ((Get-FileMagic $Dump) -ne 'PGDMP') {
            Write-Host ("$Dump is not a pg_dump custom-format dump - it looks like a plain SQL dump (beewithme_*.sql from`n" +
                        "1.7.1 or earlier), which pg_restore cannot read. Load it into a scratch database with psql, check it,`n" +
                        "then turn it into a .dump and restore that with this script. Only restore dumps you made yourself:`n" +
                        "psql runs as the database superuser and runs any \! shell command in the file.`n" +
                        "  $engine exec $container createdb -U $user -O $user ${db}_from_sql`n" +
                        "  Get-Content -Raw `"$Dump`" | $engine exec -i $container psql -v ON_ERROR_STOP=1 -1 -U $user -d ${db}_from_sql`n" +
                        "  $engine exec $container pg_dump -Fc -U $user -d ${db}_from_sql -f /tmp/from_sql.dump`n" +
                        "  $engine cp ${container}:/tmp/from_sql.dump .\from_sql.dump") -ForegroundColor Yellow
        }
        throw "pg_restore cannot read $Dump - nothing restored, database '$db' was not changed."
    }

    # 2. Restore into the side database; the live database stays as it is until this has worked.
    Write-Host "==> Restoring into $restoreDb ($engine); '$db' is not touched until that has worked" -ForegroundColor Cyan
    Invoke-Native { & $engine exec $container dropdb --if-exists --force -U $user $restoreDb }   # leftover of an interrupted run
    if ($LASTEXITCODE -ne 0) { throw "dropdb $restoreDb failed - database '$db' was not changed." }
    $restoreCreated = $true
    Invoke-Native { & $engine exec $container createdb -U $user -O $user $restoreDb }
    if ($LASTEXITCODE -ne 0) { throw "createdb $restoreDb failed - database '$db' was not changed." }
    Invoke-Native { & $engine exec $container pg_restore --exit-on-error --single-transaction --no-owner -U $user -d $restoreDb $inContainer }
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore failed - nothing restored, database '$db' was not changed (the side database $restoreDb is dropped)."
    }

    # 3. Swap: one transaction renames the current database away and the restored one into its place.
    $keptDb = "${db}_before_restore_$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
    $exists = (Invoke-Native { & $engine exec $container psql -U $user -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname = '$db'" } | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not check whether database '$db' exists - database '$db' was not changed." }
    if ($exists -eq '1') {
        $swapSql = "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid(); ALTER DATABASE $db RENAME TO $keptDb; ALTER DATABASE $restoreDb RENAME TO $db;"
    } else {
        $keptDb = $null
        $swapSql = "ALTER DATABASE $restoreDb RENAME TO $db;"
    }
    Write-Host "==> Swapping $restoreDb in as $db" -ForegroundColor Cyan
    Invoke-Native { & $engine exec $container psql -v ON_ERROR_STOP=1 -U $user -d postgres -c $swapSql } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Swapping the restored database in failed - database '$db' was not changed (is something still connected to it?)."
    }
    $restoreCreated = $false
} finally {
    # Always remove the temp dump inside the container (and a half-restored side database), also when a step failed.
    Invoke-Native { & $engine exec $container rm -f $inContainer } | Out-Null
    if ($restoreCreated) { Invoke-Native { & $engine exec $container dropdb --if-exists --force -U $user $restoreDb } | Out-Null }
}
Write-Host "==> Restored $Dump into $db" -ForegroundColor Green
if ($keptDb) {
    Write-Host "The database as it was before the restore is kept as $keptDb. Once the restored data is verified, drop it with:"
    Write-Host "  $engine exec $container dropdb -U $user $keptDb"
}

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
