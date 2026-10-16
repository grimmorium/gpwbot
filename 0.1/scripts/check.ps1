# Lustrzane odbicie check.sh dla Windowsa (brak bash/WSL na tej maszynie; decyzja D-006).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = "python"
if (Test-Path ".venv\Scripts\python.exe") { $py = Join-Path $PWD ".venv\Scripts\python.exe" }

& $py -m ruff check src tests scripts
& $py -m ruff format src tests scripts
& $py -m mypy src
& $py -m pytest --cov=gpwbot --cov-report=json:.coverage.json --cov-report=term-missing
& $py (Join-Path $PWD "scripts\check_coverage.py") ".coverage.json"

Write-Host "check.ps1: OK"
