#!/usr/bin/env python3
"""Wycina sekcje sesji (nagłówki `### Sdd`) z docs/SPEC.md do docs/sessions/.

Sekcja trwa od nagłówka `### Sdd` do następnego nagłówka `### S...` lub
do nagłówka poziomu 2 (`## ...`). Brak sekcji nie jest błędem.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SESSION_RE = re.compile(r"^###\s+(S\d{2})\b")
_ANY_S_HEADER_RE = re.compile(r"^###\s+S")


def extract_sessions(markdown: str) -> dict[str, str]:
    """Zwraca `{id sesji: treść}` dla nagłówków `### Sdd` (treść z nagłówkiem)."""
    sections: dict[str, str] = {}
    current_id: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal current_id, buf
        if current_id is not None:
            sections[current_id] = "\n".join(buf).rstrip() + "\n"
        current_id = None
        buf = []

    for line in markdown.splitlines():
        match = _SESSION_RE.match(line)
        if _ANY_S_HEADER_RE.match(line) or line.startswith("## "):
            flush()
        if match:
            current_id = match.group(1)
            buf = [line]
        elif current_id is not None:
            buf.append(line)
    flush()
    return sections


def split_spec(spec_path: Path, out_dir: Path) -> list[Path]:
    """Zapisuje sekcje sesji jako `out_dir/Sdd.md`; zwraca listę ścieżek."""
    text = spec_path.read_text(encoding="utf-8")
    sections = extract_sessions(text)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for session_id in sorted(sections):
        target = out_dir / f"{session_id}.md"
        target.write_text(sections[session_id], encoding="utf-8")
        written.append(target)
    return written


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    spec_path = root / "docs" / "SPEC.md"
    if not spec_path.is_file():
        print(f"brak pliku: {spec_path}", file=sys.stderr)
        return 1
    written = split_spec(spec_path, root / "docs" / "sessions")
    for path in written:
        print(path.relative_to(root))
    if not written:
        print("nie znaleziono sekcji sesji (### Sdd)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
