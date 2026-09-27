[CmdletBinding()]
param(
    [string]$Python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" }),
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RestoreArgs
)

$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "restore.py"
& $Python $Script @RestoreArgs
exit $LASTEXITCODE
