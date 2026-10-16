"""Sprawdza pokrycie linii wg progów A4: moduły logiki >= 90%, reszta >= 75%.

Odczytuje raport JSON z `pytest --cov-report=json` (domyślnie .coverage.json).
Lista modułów logiki rośnie wraz z sesjami (features, risk, ledger/fifo,
fill_model, metrics, calendar, ticks).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LOGIC_BASENAMES = {
    "features.py",
    "fifo.py",
    "fill_model.py",
    "metrics.py",
    "calendar.py",
    "ticks.py",
}


def _is_logic(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(part == "risk" for part in parts[:-1]) or Path(path).name in LOGIC_BASENAMES


def main(report_path: str | None = None) -> int:
    report_file = Path(report_path if report_path is not None else ".coverage.json")
    data = json.loads(report_file.read_text(encoding="utf-8"))
    failures: list[str] = []
    checked = 0
    for rel, file_data in sorted(data["files"].items()):
        summary = file_data.get("summary", {})
        percent = float(summary.get("percent_covered", 0.0))
        threshold = 90.0 if _is_logic(rel) else 75.0
        checked += 1
        ok = percent >= threshold - 1e-9
        status = "OK" if ok else "NIEDOSTATECZNE"
        print(f"{rel}: {percent:.1f}% (próg {threshold:.0f}%) [{status}]")
        if not ok:
            failures.append(rel)
    if checked == 0:
        print("brak danych o pokryciu", file=sys.stderr)
        return 1
    if failures:
        print(f"pokrycie poniżej progu w: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
