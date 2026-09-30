# Lint and format-check. Usage: .\scripts\lint.ps1   (add -Fix to auto-fix)
param([switch]$Fix)
$ErrorActionPreference = "Stop"
$ruff = "$PSScriptRoot\..\.venv\Scripts\ruff.exe"
if ($Fix) {
    & $ruff check --fix .
    & $ruff format .
} else {
    & $ruff check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $ruff format --check .
}
exit $LASTEXITCODE
