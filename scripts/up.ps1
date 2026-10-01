# Build and start the stack in the background, then wait for the API and the UI.
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
if (-not (Test-Path .env)) {
    Write-Error "No .env found. Run: Copy-Item .env.example .env  (then set POSTGRES_PASSWORD)"
}
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

function Wait-Url($url, $name, $logs) {
    for ($i = 0; $i -lt 45; $i++) {
        try {
            $null = Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing
            Write-Host "$name ready: $url"
            return
        } catch { Start-Sleep -Seconds 2 }
    }
    Write-Error "$name did not become healthy. Check: docker compose logs $logs"
}

Wait-Url "http://127.0.0.1:8000/health" "API" "api"
Wait-Url "http://127.0.0.1:8501/_stcore/health" "UI" "frontend"
Write-Host "Open PersonaOS: http://localhost:8501"
