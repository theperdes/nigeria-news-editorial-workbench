# Manual Windows wrapper. No scheduler is installed or assumed.
param([string]$RunDate = (Get-Date -Format "yyyy-MM-dd"))
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Create .venv and install requirements.txt before live collection."
}
$env:PYTHONUTF8 = "1"
& $Python (Join-Path $ProjectDir "scripts\collect.py") --run-date $RunDate
exit $LASTEXITCODE
