# INTERFACES — publiczne sygnatury ukończonych modułów

Późniejsze sesje opierają się na tym pliku zamiast czytać źródła (A2). Format: klasa/funkcja + jedno zdanie.

## S00

### `gpwbot.config`

- `AppConfig` — frozen model pydantic z polami: `timezone`, `paths`, `market`, `portfolio`,
  `universe`, `runs`, `llm`, `risk`, `costs`, `screener`, `baseline`, `text`, `eval`;
  procenty/kwoty jako `Decimal`.
- `AppConfig.config_hash() -> str` — SHA-256 kanonicznego JSON (posortowane klucze).
- `load_config(path: str | Path, env: Mapping[str, str] | None = None) -> AppConfig` — ładuje YAML,
  nadpisuje zmiennymi `GPWBOT__SEKCJA__KLUCZ`, rozwiązuje ścieżki względem pliku, waliduje;
  błędy jako `ConfigError`.
- Modele sekcji: `PathsCfg(db_path, data_dir, raw_dir, reports_dir)`, `MarketCfg` (godziny faz,
  `settlement_days`, `settle_instantly`, `price_limit_pct`), `PortfolioDef(id, strategy)`,
  `RunCfg(name, hour, minute)`, `LlmCfg`, `RiskCfg`, `CostsCfg`, `ScreenerCfg`, `BaselineCfg`,
  `TextCfg`, `EvalCfg`.

### `gpwbot.clock`

- `Clock` — Protocol z metodą `now() -> datetime` (aware).
- `SystemClock.now() -> datetime` — bieżący czas UTC, aware.
- `FakeClock(start: datetime)` — deterministyczny zegar; metody `advance(delta)`, `set(ts)`;
  start/`ts` bez strefy rzuca `ValueError`.

### `gpwbot.context`

- `AppContext` — frozen dataclass; pola S00: `config: AppConfig`, `clock: Clock`.
- `build_context(config_path, *, clock=None, env=None, **overrides) -> AppContext` — buduje kontekst;
  nieznane pola w `overrides` rzucają `TypeError`.

### `gpwbot.logging_setup`

- `setup_logging(level: str = "INFO", json_output: bool = False) -> None` — konfiguruje structlog
  (UTC ISO timestamp, poziom, zmienne kontekstowe; JSON na stdout przy `json_output=True`).
- `bind_run(run_id: str) -> Token[str | None]` — wiąże `run_id` przez contextvar.
- `unbind_run(token: Token[str | None]) -> None` — resetuje wiązanie.

### `gpwbot.errors`

- Hierarchia: `GpwbotError` → `ConfigError`, `DataQualityError`, `ProviderError`, `LlmError`,
  `LlmValidationError(LlmError)`, `RiskViolation`, `LedgerError`, `RunLockError`, `ReplayMismatch`.

### `gpwbot.cli` (Typer, punkt wejścia `gpwbot`)

- Opcja globalna: `--config PATH` (domyślnie `config/config.yaml`).
- `version` — wypisuje wersję; kod 0.
- `doctor` — ładuje konfigurację, wypisuje hash i ścieżki; kod 1 przy błędzie konfiguracji.
- Stuby (kod 2): `ingest --since`, `backfill-prices --years`, `run NAME [--as-of] [--dry-run]`,
  `replay --run-id`, `report --date`, `eval --from`.

### `scripts/split_spec.py`

- `extract_sessions(markdown: str) -> dict[str, str]` — wycina sekcje `### Sdd` (do następnego
  `### S...` lub nagłówka `## `).
- `split_spec(spec_path: Path, out_dir: Path) -> list[Path]` — zapisuje sekcje jako pliki.
