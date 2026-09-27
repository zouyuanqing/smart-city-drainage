[CmdletBinding()]
param(
    [string]$Python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" }),
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BackupArgs
)

$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "backup.py"
& $Python $Script @BackupArgs
exit $LASTEXITCODE
