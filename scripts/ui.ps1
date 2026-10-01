# Run the Streamlit UI locally (outside Docker) against the API on port 8000.
# It runs from frontend/ so frontend/.streamlit/config.toml (theme, fonts) applies.
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\.."
Set-Location "$root\frontend"
& "$root\.venv\Scripts\streamlit.exe" run streamlit_app.py @args
exit $LASTEXITCODE
