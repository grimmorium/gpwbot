"""Kontekst aplikacji — jedyny sposób przekazywania zależności (A3).

Pola: `config`, `clock` (S00) i `session_factory` (S03, D-021). Kolejne sesje
dodają pola (`calendar`, `llm`, `notifier`) i rozszerzają `build_context`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from gpwbot.clock import Clock, SystemClock
from gpwbot.config import AppConfig, load_config
from gpwbot.db.session import create_engine_for, make_session_factory


@dataclass(frozen=True)
class AppContext:
    config: AppConfig
    clock: Clock
    session_factory: sessionmaker[Session]


def build_context(
    config_path: str | Path,
    *,
    clock: Clock | None = None,
    env: Mapping[str, str] | None = None,
    **overrides: object,
) -> AppContext:
    """Buduje `AppContext`; `overrides` może zastąpić istniejące pola.

    Nieznane nazwy pól rzucają `TypeError`. Dozwolone pola (S03): `config`,
    `clock`, `session_factory`. Domyślna fabryka sesji powstaje z bazy z
    konfiguracji (`create_engine_for` + `make_session_factory`).
    """
    config = load_config(config_path, env=env)
    known = {field.name for field in fields(AppContext)}
    unknown = set(overrides) - known
    if unknown:
        raise TypeError(f"nieznane pola AppContext: {', '.join(sorted(unknown))}")

    resolved_clock: Clock = clock if clock is not None else SystemClock()
    factory: sessionmaker[Session] | None = None
    for name, value in overrides.items():
        if name == "config":
            config = cast(AppConfig, value)
        elif name == "clock":
            resolved_clock = cast(Clock, value)
        elif name == "session_factory":
            factory = cast(sessionmaker[Session], value)
    if factory is None:
        factory = make_session_factory(create_engine_for(config))
    return AppContext(config=config, clock=resolved_clock, session_factory=factory)
