# Stub backup.ps1 for the start-script tests: logs its arguments, fails when BWM_STUB_BACKUP_FAIL=1,
# otherwise writes a marker like the real script so start.ps1 can name the dump.
param([string]$OutDir, [int]$Keep = 30, [string]$Container)
$ErrorActionPreference = 'Stop'
Add-Content -LiteralPath $env:BWM_STUB_LOG -Value "backup -OutDir $OutDir -Container $Container"
if ($env:BWM_STUB_BACKUP_FAIL -eq '1') { throw 'stub backup failed' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Set-Content -LiteralPath (Join-Path $OutDir 'last-backup.json') -Value '{"dump": "beewithme_stub.dump"}'
Write-Host 'STUB BACKUP DONE'
