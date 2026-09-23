$ErrorActionPreference = "Stop"

python -m unittest -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name Kontur `
    desktop.py

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Built: dist\Kontur.exe"
