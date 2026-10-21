"""Konfiguracja aplikacji: modele pydantic v2, ładowanie z YAML i środowiska.

A3: procenty i kwoty jako `Decimal`; modele frozen z `extra=forbid`.
Nadpisywania ze środowiska: `GPWBOT__SEKCJA__KLUCZ` (decyzja D-002).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gpwbot.errors import ConfigError

ENV_PREFIX = "GPWBOT__"

Pct = Annotated[Decimal, Field(ge=0, le=100)]
Ratio = Annotated[Decimal, Field(ge=0, le=1)]


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PathsCfg(_Cfg):
    """Ścieżki robocze; względne są rozwiązywane względem pliku konfiguracji."""

    db_path: Path
    data_dir: Path
    raw_dir: Path
    reports_dir: Path


class MarketCfg(_Cfg):
    # Domyślne godziny faz GPW zweryfikowane w S02 wg SZOG §5(1)(1) (D-014).
    preopen_start: dt.time = dt.time(8, 30)
    preopen_end: dt.time = dt.time(9, 0)
    continuous_start: dt.time = dt.time(9, 0)
    continuous_end: dt.time = dt.time(16, 50)
    close_auction_start: dt.time = dt.time(16, 50)
    close_auction_end: dt.time = dt.time(17, 0)
    settlement_days: int = Field(default=2, ge=0)
    settle_instantly: bool = False
    price_limit_pct: Pct = Decimal("10")

    @model_validator(mode="after")
    def _check_phases(self) -> MarketCfg:
        if not (
            self.preopen_start
            <= self.preopen_end
            <= self.continuous_start
            <= self.continuous_end
            <= self.close_auction_start
            <= self.close_auction_end
        ):
            raise ValueError("godziny faz sesji muszą być nie malejące")
        return self


class PortfolioDef(_Cfg):
    id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)


class PortfolioCfg(_Cfg):
    initial_cash_pln: Decimal = Field(default=Decimal("100000"), ge=0)
    benchmarks: list[str] = ["wig", "wig20"]
    portfolios: list[PortfolioDef] = []


class UniverseCfg(_Cfg):
    indices: list[str] = ["wig20", "mwig40"]
    min_avg_turnover_pln_20d: Decimal = Field(default=Decimal("500000"), ge=0)
    # Minimum sesji historii (Część I sekcja 8); dodane w S04 — D-027.
    min_history_sessions: int = Field(default=250, gt=0)
    min_price_pln: Decimal = Field(default=Decimal("1"), gt=0)
    exclude_tickers: list[str] = []


class RunCfg(_Cfg):
    name: str = Field(min_length=1)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


class LlmCfg(_Cfg):
    base_url: str = "http://localhost:8080/v1"
    temperature: float = Field(default=0.2, ge=0)
    max_prompt_tokens: int = Field(default=6000, gt=0)
    samples_per_decision: int = Field(default=1, ge=1, le=5)
    thinking_extraction: bool = False
    thinking_decision: bool = True


class RiskCfg(_Cfg):
    max_position_pct: Pct = Decimal("10")
    max_positions: int = Field(default=10, gt=0)
    min_cash_pct: Pct = Decimal("10")
    max_sector_pct: Pct = Decimal("30")
    max_daily_turnover_pct: Pct = Decimal("30")
    min_trade_value_pln: Decimal = Field(default=Decimal("2000"), gt=0)
    max_adv_participation_pct: Pct = Decimal("5")
    stop_atr_mult: float = Field(default=2.5, gt=0)
    min_holding_sessions: int = Field(default=3, ge=0)
    cooldown_after_sell_sessions: int = Field(default=5, ge=0)
    kill_switch_drawdown_pct: Pct = Decimal("15")


class CostsCfg(_Cfg):
    commission_pct: Pct = Decimal("0.30")
    commission_min_pln: Decimal = Field(default=Decimal("5"), ge=0)
    slippage_bps: int = Field(default=15, ge=0)
    # Podatek od dywidendy (sekcja 2 Czesci I); uzywany w S07/S09 — D-019.
    dividend_tax_pct: Pct = Decimal("19")


class StooqCfg(_Cfg):
    """Parametry adaptera cen Stooq (S03); limity wyłącznie z konfiguracji."""

    requests_per_second: float = Field(default=1.0, gt=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=1)
    retry_backoff_base_s: float = Field(default=0.5, ge=0)


class YfinanceCfg(_Cfg):
    """Zapasowy adapter yfinance — nieoficjalny, domyślnie wyłączony (V-03)."""

    enabled: bool = False


class ProvidersCfg(_Cfg):
    stooq: StooqCfg = StooqCfg()
    yfinance: YfinanceCfg = YfinanceCfg()


class ScreenerCfg(_Cfg):
    """Parametry screenera (S05); wagi scoringu wyłącznie z konfiguracji (D-032)."""

    top_n: int = Field(default=25, gt=0)
    max_total: int = Field(default=40, gt=0)
    materiality_threshold: Ratio = Decimal("0.6")
    weight_mom_6m: float = Field(default=1.0, ge=0)
    weight_mom_12_1: float = Field(default=1.0, ge=0)
    weight_trend: float = Field(default=1.0, ge=0)
    weight_low_vol: float = Field(default=1.0, ge=0)
    weight_vol_z: float = Field(default=1.0, ge=0)


# Placeholder — pola rozszerzane w S09/S14 (decyzja D-001).
class BaselineCfg(_Cfg):
    top_k: int = Field(default=10, gt=0)
    rebalance_sessions: int = Field(default=5, ge=1)


# Placeholder — pola rozszerzane w S10–S12 (decyzja D-001).
class TextCfg(_Cfg):
    dedupe_similarity: int = Field(default=92, ge=0, le=100)
    extraction_temperature: float = Field(default=0.0, ge=0)


# Placeholder — pola rozszerzane w S14 (decyzja D-001).
class EvalCfg(_Cfg):
    min_forward_days: int = Field(default=90, gt=0)


class AppConfig(_Cfg):
    timezone: str = "Europe/Warsaw"
    paths: PathsCfg
    market: MarketCfg = MarketCfg()
    portfolio: PortfolioCfg = PortfolioCfg()
    universe: UniverseCfg = UniverseCfg()
    runs: list[RunCfg] = [
        RunCfg(name="morning", hour=8, minute=15),
        RunCfg(name="evening", hour=17, minute=30),
    ]
    llm: LlmCfg = LlmCfg()
    risk: RiskCfg = RiskCfg()
    costs: CostsCfg = CostsCfg()
    providers: ProvidersCfg = ProvidersCfg()
    screener: ScreenerCfg = ScreenerCfg()
    baseline: BaselineCfg = BaselineCfg()
    text: TextCfg = TextCfg()
    eval: EvalCfg = Field(default_factory=EvalCfg)

    @model_validator(mode="after")
    def _check_unique(self) -> AppConfig:
        run_names = [run.name for run in self.runs]
        if len(set(run_names)) != len(run_names):
            raise ValueError("nazwy runów muszą być unikalne")
        portfolio_ids = [p.id for p in self.portfolio.portfolios]
        if len(set(portfolio_ids)) != len(portfolio_ids):
            raise ValueError("identyfikatory portfeli muszą być unikalne")
        return self

    def config_hash(self) -> str:
        """SHA-256 kanonicznego JSON (posortowane klucze, stałe separatory)."""
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_validation_error(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()]
    return "błąd walidacji konfiguracji: " + "; ".join(parts)


def _section_models() -> dict[str, type[BaseModel]]:
    """Sekcje `AppConfig` będące modelami (klucz = nazwa małą literą)."""
    models: dict[str, type[BaseModel]] = {}
    for name, field in AppConfig.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            models[name.lower()] = annotation
    return models


_SECTION_MODELS = _section_models()


def _apply_env_overrides(raw: dict[str, Any], env: Mapping[str, str]) -> None:
    """Nadpisuje wartości ze zmiennych `GPWBOT__SEKCJA__KLUCZ` (decyzja D-002).

    Sekcja nieobecna w pliku może zostać utworzona, jeśli jest znana schematowi;
    nieznane sekcje i klucze rzucają `ConfigError` (spójnie z `extra=forbid`).
    """
    for name in sorted(env):
        if not name.startswith(ENV_PREFIX):
            continue
        parts = name[len(ENV_PREFIX) :].split("__")
        if len(parts) != 2 or not all(parts):
            raise ConfigError(
                f"nieprawidłowa zmienna środowiskowa: {name} (oczekiwano GPWBOT__SEKCJA__KLUCZ)"
            )
        section, key = parts[0].lower(), parts[1].lower()
        section_model = _SECTION_MODELS.get(section)
        if section_model is None:
            raise ConfigError(f"zmienna {name}: '{section}' nie jest sekcją konfiguracji")
        known_keys = {field.lower() for field in section_model.model_fields}
        if key not in known_keys:
            raise ConfigError(
                f"zmienna {name}: klucz '{key}' nie istnieje w sekcji '{section}' (extra=forbid)"
            )
        target = raw.get(section)
        if target is None:
            target = {}
            raw[section] = target
        if not isinstance(target, dict):
            raise ConfigError(f"zmienna {name}: sekcja '{section}' w pliku nie jest mapą")
        target[key] = env[name]


def _resolve_paths(raw: dict[str, Any], base_dir: Path) -> None:
    paths_data = raw.get("paths")
    if not isinstance(paths_data, dict):
        return
    for key in ("db_path", "data_dir", "raw_dir", "reports_dir"):
        value = paths_data.get(key)
        if value is None:
            continue
        candidate = Path(str(value))
        if not candidate.is_absolute():
            paths_data[key] = base_dir / candidate


def load_config(path: str | Path, env: Mapping[str, str] | None = None) -> AppConfig:
    """Ładuje konfigurację z YAML; `env=None` oznacza `os.environ`.

    Kolejność: YAML → nadpisywania ze środowiska → walidacja pydantic.
    Ścieżki względne są rozwiązywane względem katalogu pliku konfiguracji.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"plik konfiguracji nie istnieje: {config_path}")
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"błędny YAML w {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("plik konfiguracji musi być mapą")

    env_map = os.environ if env is None else dict(env)
    _apply_env_overrides(raw, env_map)
    _resolve_paths(raw, config_path.resolve().parent)

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc)) from exc
