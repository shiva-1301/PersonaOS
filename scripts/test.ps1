# Run the test suite (live-provider tests are excluded by default).
# Usage: .\scripts\test.ps1            (extra args are passed to pytest, e.g. -k health)
$ErrorActionPreference = "Stop"
& "$PSScriptRoot\..\.venv\Scripts\python.exe" -m pytest @args
exit $LASTEXITCODE
