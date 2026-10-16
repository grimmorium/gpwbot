"""Logowanie (structlog) i kontekst runu przez `contextvars` (A3).

Procesory: znacznik czasu UTC ISO, poziom logowania, zmienne kontekstowe.
Zakaz `print` poza CLI — komponenty używają `structlog.get_logger()`.
"""

from __future__ import annotations

import logging as stdlib_logging
from contextvars import ContextVar, Token
from typing import Any

import structlog

_RUN_ID: ContextVar[str | None] = ContextVar("gpwbot_run_id", default=None)


def bind_run(run_id: str) -> Token[str | None]:
    """Wiąże `run_id` z bieżącym kontekstem; zwraca token do resetu."""
    return _RUN_ID.set(run_id)


def unbind_run(token: Token[str | None]) -> None:
    _RUN_ID.reset(token)


def _inject_run_id(logger: object, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    run_id = _RUN_ID.get()
    if run_id is not None:
        event_dict["run_id"] = run_id
    return event_dict


_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LEVELS = {name: getattr(stdlib_logging, name) for name in _LEVEL_NAMES}


def setup_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Konfiguruje structlog; `json_output=True` daje JSON na stdout."""
    numeric_level = _LEVELS.get(level.upper())
    if numeric_level is None:
        raise ValueError(f"nieznany poziom logowania: {level}")
    processors: list[Any] = [
        _inject_run_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=False))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
