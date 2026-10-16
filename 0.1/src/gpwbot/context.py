"""Kontekst aplikacji — jedyny sposób przekazywania zależności (A3).

W S00 pola: `config`, `clock`. Kolejne sesje dodają pola (`session_factory`,
`calendar`, `llm`, `notifier`) i rozszerzają `build_context`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import cast

from gpwbot.clock import Clock, SystemClock
from gpwbot.config import AppConfig, load_config


@dataclass(frozen=True)
class AppContext:
    config: AppConfig
    clock: Clock


def build_context(
    config_path: str | Path,
    *,
    clock: Clock | None = None,
    env: Mapping[str, str] | None = None,
    **overrides: object,
) -> AppContext:
    """Buduje `AppContext`; `overrides` może zastąpić istniejące pola.

    Nieznane nazwy pól rzucają `TypeError`. W S00 dozwolone pola to
    `config` i `clock`.
    """
    config = load_config(config_path, env=env)
    known = {field.name for field in fields(AppContext)}
    unknown = set(overrides) - known
    if unknown:
        raise TypeError(f"nieznane pola AppContext: {', '.join(sorted(unknown))}")

    resolved_clock: Clock = clock if clock is not None else SystemClock()
    for name, value in overrides.items():
        if name == "config":
            config = cast(AppConfig, value)
        elif name == "clock":
            resolved_clock = cast(Clock, value)
    return AppContext(config=config, clock=resolved_clock)
