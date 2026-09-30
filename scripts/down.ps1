# Stop the stack. Data volumes are kept; add -Volumes to delete them (irreversible).
param([switch]$Volumes)
Set-Location "$PSScriptRoot\.."
if ($Volumes) { docker compose down -v } else { docker compose down }
exit $LASTEXITCODE
