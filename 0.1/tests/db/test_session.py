"""Testy `session.py` (S01): PRAGMA na połączeniu i semantyka `session_scope`."""

from __future__ import annotations

import pytest

from gpwbot.db.models import Instrument
from gpwbot.db.session import session_scope


class TestPragma:
    def test_foreign_keys_i_wal(self, tmp_db):
        engine, _ = tmp_db
        with engine.connect() as conn:
            fk = conn.exec_driver_sql("PRAGMA foreign_keys").one()[0]
            journal_mode = conn.exec_driver_sql("PRAGMA journal_mode").one()[0]
        assert int(fk) == 1
        assert str(journal_mode).lower() == "wal"


class TestSessionScope:
    def test_commit_na_koncu(self, tmp_db):
        _, factory = tmp_db
        with session_scope(factory) as s:
            s.add(Instrument(ticker="PKN"))
        # wiersz widoczny w nowej sesji (commit się odbył)
        with factory() as s2:
            assert s2.get(Instrument, 1).ticker == "PKN"

    def test_rollback_przy_wyjatku(self, tmp_db):
        _, factory = tmp_db
        with pytest.raises(RuntimeError, match="boom"), session_scope(factory) as s:
            s.add(Instrument(ticker="XXX"))
            raise RuntimeError("boom")
        # po rollbacku brak wiersza
        with factory() as s2:
            assert s2.get(Instrument, 1) is None

    def test_wyjatek_przechodzi_dalzej(self, tmp_db):
        """Wyjątek z ciała nie jest połykany przez `session_scope`."""
        _, factory = tmp_db
        with pytest.raises(RuntimeError, match="boom"), session_scope(factory):
            raise RuntimeError("boom")
