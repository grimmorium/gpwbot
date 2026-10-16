#!/usr/bin/env bash
# Kontrola jakości gpwbot (A1.4): ruff check -> ruff format -> mypy src -> pytest + pokrycie.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# układ venv na Windows (git-bash): .venv/Scripts/python.exe
if [ -x .venv/Scripts/python.exe ]; then PY=".venv/Scripts/python.exe"; fi

"$PY" -m ruff check src tests scripts
"$PY" -m ruff format src tests scripts
"$PY" -m mypy src
"$PY" -m pytest --cov=gpwbot --cov-report=json:.coverage.json --cov-report=term-missing
"$PY" scripts/check_coverage.py .coverage.json

echo "check.sh: OK"
