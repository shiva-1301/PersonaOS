# Build and start the stack in the background, then wait for /health.
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
if (-not (Test-Path .env)) {
    Write-Error "No .env found. Run: Copy-Item .env.example .env  (then set POSTGRES_PASSWORD)"
}
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

for ($i = 0; $i -lt 30; $i++) {
    try {
        $r = Invoke-RestMethod -Uri http://localhost:8000/health -TimeoutSec 2
        Write-Host "API healthy:" ($r | ConvertTo-Json -Compress)
        exit 0
    } catch { Start-Sleep -Seconds 2 }
}
Write-Error "API did not become healthy. Check: docker compose logs api"
