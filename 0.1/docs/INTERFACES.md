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

## S01

### `gpwbot.db.types`

- `DecimalStr(TypeDecorator[Decimal])` — kwoty/ceny; w bazie tekst `str(Decimal)`, po stronie
  Pythona `Decimal`; odrzuca `float` (`ValueError`).
- `UtcDateTime(TypeDecorator[dt.datetime])` — aware datetime; zapis w UTC, odczyt zawsze ze
  strefą `dt.UTC`; naive rzuca `ValueError`.

### `gpwbot.db.models` (SQLAlchemy 2.0)

- `Base(DeclarativeBase)` — `Base.metadata` jest źródłem prawdy dla migracji Alembic.
- Modele: `Portfolio`, `Instrument`, `PricesDaily`, `NewsItem`, `Filing`, `TextExtraction`,
  `FeatureSnapshot`, `Run`, `Decision`, `Order`, `Fill`, `TaxLot`, `CashLedgerEntry`,
  `PortfolioSnapshot`, `LlmCall` (tabele: `portfolios`, `instruments`, `prices_daily`,
  `news_items`, `filings`, `text_extractions`, `feature_snapshots`, `runs`, `decisions`,
  `orders`, `fills`, `tax_lots`, `cash_ledger`, `portfolio_snapshots`, `llm_calls`).

### `gpwbot.db.session`

- `sqlite_url(db_path: str | Path) -> str` — URL SQLite (forward slashes; `":memory:"` →
  `sqlite://`).
- `create_engine_for(cfg: AppConfig) -> Engine` — silnik dla `cfg.paths.db_path`; tworzy katalog,
  na każdym połączeniu PRAGMA: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`.
- `make_session_factory(engine: Engine) -> sessionmaker[Session]` — fabryka sesji
  (`expire_on_commit=False`).
- `session_scope(factory)` — contextmanager; commit na końcu, rollback przy wyjątku.

### `gpwbot.db.repo`

- `PriceBar(date, open, high, low, close, adj_close=None, volume=None, turnover_pln=None,
  quality_flag=None, adjusted=False)` — frozen dataclass; ceny jako `Decimal`.
- `InstrumentUpsert(ticker, isin=None, name=None, segment=None, sector=None,
  index_membership=None, active=None, aliases_json=None)` — frozen dataclass; `None` = pole nie
  jest zmieniane.
- `upsert_instruments(session, instruments) -> list[Instrument]` — upsert po tickerze (flush bez
  commitu); tickery w partii muszą być różne.
- `get_instrument_by_ticker(session, ticker) -> Instrument | None`.
- `list_instruments(session, active_only: bool = False) -> list[Instrument]`.
- `upsert_prices(session, instrument_id, bars, source, *, now=None) -> int` — upsert po PK
  (instrument_id, date, source); konflikt aktualizuje wartości i `ingested_at`; FK na
  nieistniejący instrument rzuca `IntegrityError` (D-009).
- `get_prices_df(session, instrument_id, start=None, end=None) -> pd.DataFrame` — indeks
  `DatetimeIndex` o nazwie `date`, kolumny `open high low close adj_close volume turnover
  quality_flag`; numeryczne jako float64.
- `last_price_date(session, instrument_id) -> dt.date | None`.

### `gpwbot.db.migrate`

- `upgrade_to_head(db_path: str | Path) -> None` — programowe `alembic upgrade head` (tworzy
  katalog bazy; niezależne od CWD).
- `downgrade_to_base(db_path: str | Path) -> None` — programowe `alembic downgrade base`.

### CLI (rozszerzenie S01)

- `gpwbot db upgrade` — stosuje migracje do head dla bazy z konfiguracji; kod 1 przy błędzie
  konfiguracji.

### Testy / fixture

- Globalna fixture `tmp_db` w `tests/conftest.py`: plik SQLite ze schematem (`create_all`) i
  PRAGMA jak w produkcji; zwraca `(engine, session_factory)`. Zgodność migracji z metadanymi
  sprawdzają testy w `tests/db/test_migrations.py`.

## S02

### `gpwbot.calendar`

- `SessionTimes(date, preopen_start, preopen_end, continuous_start, continuous_end,
  close_auction_start, close_auction_end)` — frozen dataclass; wszystkie godziny aware
  (Europe/Warsaw).
- `GpwCalendar(cfg: AppConfig, override_path: str | Path | None = None)` — kalendarz sesji GPW:
  exchange_calendars XWAR + nadpisywania z YAML (`holidays`/`sessions`; brak pliku = puste;
  D-017); godziny faz wyłącznie z `cfg.market`. Metody:
  - `is_session(d) -> bool` — dzień sesji po nałożeniu nadpisań.
  - `next_session_after(ts) -> dt.date` — pierwsza sesja, której otwarcie jest późniejsze niż
    `ts`; bieżąca sesja, jeśli `ts` przed jej otwarciem; naive `ts` rzuca `ValueError`.
  - `previous_session(d) -> dt.date` — ostatnia sesja ściśle przed `d` (`d` dowolny).
  - `sessions_between(a, b) -> list[dt.date]` / `count_sessions(a, b) -> int` — przedział
    półotwarty `(a, b]`; wymaga `a <= b`, inaczej `ValueError` (D-018).
  - `add_sessions(d, n) -> dt.date` — przesunięcie o `n` sesji; `d` musi być sesją.
  - `session_open_ts(d)` / `session_close_ts(d) -> dt.datetime` — otwarcie = start notowań
    ciągłych (D-013), zamknięcie = koniec aukcji zamknięcia; dzień niesesyjny rzuca
    `ValueError`.
  - `session_phases(d) -> SessionTimes`.

### `gpwbot.ticks`

- `TickBand(max_price: Decimal | None, tick: Decimal)` — frozen dataclass; cena równa
  `max_price` należy do pasma; `None` = bez górnego ograniczenia.
- `RoundMode` — Enum: `UP`, `DOWN`, `NEAREST` (tie w NEAREST → HALF_UP).
- `TickTable(groups)` / `TickTable.from_yaml(path) -> TickTable` — grupy pasm cenowych; grupa
  `default` wymagana (`ValueError`); błędy pliku/schematu jako `ConfigError`. Plik:
  `config/tick_sizes.yaml` (wartości przykładowe — V-02, D-015). Metody:
  - `tick_for(price, group="default") -> Decimal`; cena ≤ 0 i nieznana grupa rzucają
    `ValueError`.
  - `round_price(price, mode, *, group="default") -> Decimal` — dokładna arytmetyka Decimal;
    DOWN poniżej jednego ticka rzuca `ValueError`.
  - `is_valid_price(price, group="default") -> bool` — dodatnia wielokrotność ticka pasma.

### `gpwbot.costs` (czyste funkcje)

- `Side` — Enum: `BUY`, `SELL` (do ponownego użycia w S07).
- `commission(value, cfg: CostsCfg) -> Decimal` — max(wartość × pct/100, min), HALF_UP do 0,01;
  wartość 0 → 0.00 (D-016); ujemna rzuca `ValueError`.
- `apply_slippage(price, side: Side, bps: int) -> Decimal` — BUY w górę, SELL w dół; dokładny
  iloczyn (kwantyzacja do ticka należy do fill model S07); cena ≤ 0 / bps < 0 rzucają
  `ValueError`.
- `net_dividend(gross, tax_rate) -> Decimal` — brutto × (1 − stawka/100), HALF_UP do 0,01;
  stawka poza 0–100% i ujemne brutto rzucają `ValueError`.

### Konfiguracja (rozszerzenie S00/S02)

- `CostsCfg.dividend_tax_pct: Decimal = 19` — podatek od dywidendy (D-019).
- Domyślne godziny faz GPW zweryfikowane wg SZOG §5(1)(1): `preopen_start = 8:30` (dawniej
  przykładowe 8:00; D-014) — skorygowane w `MarketCfg` i `config/config.yaml`.
- Nowe pliki: `config/tick_sizes.yaml`, `config/gpw_holidays_override.yaml`; fixture testowa
  `tests/fixtures/gpw_holidays.yaml` (oficjalne dni wolne GPW 2025–2026).
