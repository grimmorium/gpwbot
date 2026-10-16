"""Testy split_spec (S00): wycinanie sekcji sesji z Markdown."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from split_spec import extract_sessions, split_spec  # noqa: E402

SAMPLE = """\
# Tytuł

## A. Zasady

### S01 — Pierwsza sesja

Treść S01.

### S02 — Druga sesja

Treść S02.

## B. Sesje implementacyjne

### S03 — Trzecia sesja

Treść S03 bez nagłówka kończącego.
"""


def test_extract_sessions_finds_all() -> None:
    sections = extract_sessions(SAMPLE)
    assert set(sections) == {"S01", "S02", "S03"}


def test_extract_sessions_content_includes_header() -> None:
    sections = extract_sessions(SAMPLE)
    assert sections["S01"].startswith("### S01")
    assert "Treść S01." in sections["S01"]
    assert "Treść S02" not in sections["S01"]


def test_extract_sessions_stops_at_level_two_header() -> None:
    sections = extract_sessions(SAMPLE)
    assert "## B." not in sections["S02"]
    assert "### S03" not in sections["S02"]


def test_split_spec_writes_files(tmp_path: Path) -> None:
    spec_file = tmp_path / "SPEC.md"
    spec_file.write_text(SAMPLE, encoding="utf-8")
    out_dir = tmp_path / "sessions"

    written = split_spec(spec_file, out_dir)

    assert [p.name for p in written] == ["S01.md", "S02.md", "S03.md"]
    assert (out_dir / "S01.md").read_text(encoding="utf-8") == sections_content("S01")


def test_split_spec_no_sections_is_not_an_error(tmp_path: Path) -> None:
    spec_file = tmp_path / "SPEC.md"
    spec_file.write_text("# Pusty dokument\n\n## Bez sesji\n", encoding="utf-8")

    written = split_spec(spec_file, tmp_path / "sessions")

    assert written == []


def test_split_spec_real_repo_has_s00_to_s15() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    spec_file = repo_root / "docs" / "SPEC.md"
    sections = extract_sessions(spec_file.read_text(encoding="utf-8"))
    assert {f"S{i:02d}" for i in range(16)} <= set(sections)


def sections_content(session_id: str) -> str:
    return extract_sessions(SAMPLE)[session_id]
