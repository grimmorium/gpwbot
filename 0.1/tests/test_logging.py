"""Testy logowania (S00): JSON z timestamp/level/event, bind_run."""

from __future__ import annotations

import contextlib
import io
import json

import pytest
import structlog

from gpwbot.logging_setup import bind_run, setup_logging, unbind_run


def _capture_log(event: str) -> dict[str, object]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        logger = structlog.get_logger("test")
        logger.info(event)
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, f"oczekiwano jednego wiersza logu, gotowe: {lines!r}"
    return json.loads(lines[0])


def test_json_output_has_required_fields() -> None:
    setup_logging("INFO", json_output=True)
    record = _capture_log("hello")
    assert "timestamp" in record
    assert record["level"] == "info"
    assert record["event"] == "hello"


def test_timestamp_is_utc_iso() -> None:
    setup_logging("INFO", json_output=True)
    record = _capture_log("x")
    timestamp = str(record["timestamp"])
    # ISO 8601 z przesunięciem UTC (+00:00 lub Z)
    assert timestamp.endswith("+00:00") or timestamp.endswith("Z")


def test_bind_run_adds_run_id() -> None:
    setup_logging("INFO", json_output=True)
    token = bind_run("run-42")
    try:
        record = _capture_log("inside run")
        assert record["run_id"] == "run-42"
    finally:
        unbind_run(token)


def test_unbind_run_removes_run_id() -> None:
    setup_logging("INFO", json_output=True)
    token = bind_run("run-7")
    unbind_run(token)
    record = _capture_log("outside run")
    assert "run_id" not in record


def test_console_renderer_outputs_plain_text() -> None:
    setup_logging("INFO", json_output=False)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        structlog.get_logger("test").info("console event")
    output = buffer.getvalue()
    assert "console event" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output.strip())


def test_unknown_level_rejected() -> None:
    with pytest.raises(ValueError, match="poziom logowania"):
        setup_logging("LOUD", json_output=True)
