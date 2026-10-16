# Nigeria News Workbench launcher
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment was not found: $Python"
}

$env:PYTHONUTF8 = "1"
Set-Location $ProjectDir
& $Python "src\news_ui.py"
exit $LASTEXITCODE
