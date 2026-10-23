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

## S03

### `gpwbot.providers.base`

- `PriceBar(date, open, high, low, close, adj_close=None, volume=None,
  turnover_pln=None)` — frozen dataclass; bar po stronie źródła (ceny `Decimal`, PLN);
  `turnover_pln` poza listą pól specyfikacji S03 (D-022).
- `PriceProvider` — Protocol: `get_daily(symbol, start, end) -> list[PriceBar]`.

### `gpwbot.providers.prices_stooq`

- `STOOQ_DAILY_URL = "https://stooq.pl/q/d/l/"` — wzorzec do weryfikacji (V-03).
- `parse_stooq_csv(text) -> list[PriceBar]` — czysty parser CSV: nagłówki PL/EN, zmienna
  liczba kolumn, przecinek dziesiętny (zacytowany i niezacytowany; D-024); pusta odpowiedź /
  komunikat zamiast danych → `ProviderError`.
- `load_symbol_map(path) -> dict[str, str]` — nadpisywania ticker→symbol (klucze uppercase);
  brak pliku = pusta mapa; błędy jako `ConfigError`.
- `map_symbol(ticker, overrides) -> str` — nadpisanie z mapy albo ticker małymi literami.
- `RateLimiter(requests_per_second)` — `.wait()` nie częściej niż limit (monotoniczny zegar).
- `StooqPriceProvider(cfg: ProvidersCfg, symbol_map_path=..., client=None)`:
  - `symbol_for(ticker) -> str`; `close()`.
  - `fetch_raw(symbol, start, end) -> str` — surowa odpowiedź; retry 5xx/timeouty (tenacity,
    backoff z configu), 4xx → natychmiast `ProviderError`, po wyczerpaniu prób
    `ProviderError("… N nieudanych prób …")` (D-025).
  - `get_daily(symbol, start, end) -> list[PriceBar]` — protokół `PriceProvider`.

### `gpwbot.providers.prices_yfinance`

- `symbol_for(ticker) -> str` — sufiks `.WA` (np. `PKN.WA`).
- `bars_from_yf_df(df) -> list[PriceBar]` — czysta konwersja DataFrame'y yfinance; brak
  kolumny `Adj Close` → `adj_close=None`; brak OHLC → `ProviderError`.
- `YFinancePriceProvider(cfg)` — rzuca `ConfigError`, jeśli `providers.yfinance.enabled=False`
  (domyślnie wyłączony); `get_daily` importuje `yfinance` leniwie (opcjonalna zależność).

### `gpwbot.quality`

- `QualityIssue(rule, date, message, flag=None)` — frozen dataclass; reguły:
  `non_positive_price`, `high_below_low`, `ohlc_out_of_range`, `duplicate_date`, `gap`,
  `large_change` (flaga `needs_review`).
- `LARGE_CHANGE_PCT = Decimal("25")` — próg anomalii dziennego skoku (D-023).
- `check_bars(df, calendar) -> list[QualityIssue]` — reguły jakości; df w kształcie
  `repo.get_prices_df` (indeks `date`, kolumny OHLC + opcjonalnie `adj_close`); luki liczone
  względem sesji GPW (święta/weekendy nie są lukami); skok liczony na `adj_close`, jeśli
  dostępne. Brak wymaganych kolumn → `ValueError`.
- `is_fresh(last_date, as_of, calendar) -> bool` — świeżość wobec ostatniej w pełni
  zakończonej sesji (w trakcie bieżącej sesji jej świeca nie jest wymagana); `None` → False;
  naive `as_of` → `ValueError`.

### `gpwbot.ingest.prices_job`

- `TickerResult(ticker, ok, bars_fetched=0, bars_upserted=0, error=None, issues=())`.
- `IngestReport(start, end, results)` — właściwości: `ok_tickers`, `failed_tickers` (ticker→błąd),
  `total_bars_upserted`, `total_issues`.
- `ingest_prices(ctx, tickers, start, end) -> IngestReport` — pobranie → cache surowej
  odpowiedzi (`raw_dir/stooq/<ticker>/<data warszawska>.csv`) → parsowanie → jakość (flaga
  `needs_review` trafia do bazy) → upsert; obrót = wartość z CSV albo `close × volume`;
  błąd tickera nie przerywa pozostałych.
- `backfill_prices(ctx, years, tickers=None) -> IngestReport` — partie roczne od tej samej daty
  `years` lat wstecz do dnia bieżącego (zegar kontekstu); domyślnie `default_price_tickers`.
- `default_price_tickers(cfg) -> list[str]` — `universe.indices ∪ portfolio.benchmarks`
  (do wymiany po S04; D-026).

### Konfiguracja i kontekst (rozszerzenie S03)

- `ProvidersCfg(stooq, yfinance)`; `StooqCfg(requests_per_second=1.0, timeout_seconds=30.0,
  max_retries=3, retry_backoff_base_s=0.5)`; `YfinanceCfg(enabled=False)` — nowe sekcje w
  `AppConfig` (`providers`, z domyślnymi wartościami).
- `AppContext` zyskuje pole `session_factory: sessionmaker[Session]` (D-021);
  `build_context(..., session_factory=...)` akceptuje override; domyślnie fabryka powstaje z
  bazy z konfiguracji (migracje NIE są stosowane — `gpwbot db upgrade`).
- Nowe pliki: `config/symbol_map.yaml` (nadpisywania symboli Stooq; indeksy do weryfikacji,
  V-03), sekcja `providers` w `config/config.yaml`; fixtures `tests/fixtures/stooq_pkn.csv`,
  `stooq_jump.csv`.

### CLI (rozszerzenie S03)

- `gpwbot ingest --prices [--since Nd]` — ingest cen ze Stooq dla tickerów z konfiguracji;
  kod 1 przy błędzie konfiguracji/kształtu `--since`; bez `--prices` nadal stub (kod 2).
- `gpwbot backfill-prices --years N` — backfill partiami rocznymi.

## S04

### `gpwbot.universe`

- `ExclusionReason` — `StrEnum`: `INACTIVE`, `SUSPENDED`, `LOW_TURNOVER`, `LOW_PRICE`,
  `SHORT_HISTORY`, `NEEDS_REVIEW`, `STALE`; wartości to małe litery (`"inactive"` itd.) (D-031).
- `SeedInstrument(ticker, isin=None, name=None, segment=None, sector=None, indices=(),
  tick_group="default", aliases=())` — frozen dataclass; wpisu pliku seedu.
- `SyncReport(synced, deactivated)` — frozen dataclass; wynik syncu.
- `UniverseResult(members: tuple[str, ...], excluded: dict[str, ExclusionReason])` — frozen
  dataclass; członkowie i wykluczenia posortowane po tickerze (deterministyczne).
- `DEFAULT_SEED_PATH = Path("config/universe_seed.yaml")` — względem CWD (wzorzec D-017/D-025);
  `TURNOVER_WINDOW_SESSIONS = 20`.
- `load_seed(path) -> list[SeedInstrument]` — walidacja pliku seedu (lista map; tickery →
  uppercase, indeksy → lowercase; duplikaty tickerów i nieznane klucze → `ConfigError`);
  brak pliku = błąd (D-030).
- `sync_instruments(ctx, seed_path=DEFAULT_SEED_PATH) -> SyncReport` — upsert seedu do tabeli
  `instruments` (`active=true`) + `active=false` dla istniejących instrumentów spoza seedu
  (bez kasowania); idempotentne.
- `get_universe(ctx, as_of: dt.datetime) -> UniverseResult` — członkowie i powody wykluczeń;
  kandydaci = `index_membership ∩ cfg.universe.indices`, poza `exclude_tickers`; dane tylko o
  dacie ≤ `as_of` (bez look-ahead). Kolejność filtrów (pierwszy trafiony): INACTIVE → SUSPENDED
  (ostatnia świeca: volume=0 i obrót None/≤0; D-028) → STALE (`quality.is_fresh`) → SHORT_HISTORY
  (< `min_history_sessions`; brak cen = ten powód) → LOW_TURNOVER (średnia obrotu ostatnich 20
  barów < próg, Decimal) → LOW_PRICE (ostatnie close < `min_price_pln`) → NEEDS_REVIEW (dowolna
  świeca z flagą). Naive `as_of` → `ValueError`.

### `gpwbot.aliases`

- `AliasEntry(alias, tickers: tuple[str, ...], ambiguous=False, exact_case=False)` — frozen
  dataclass; tickery normalizowane do uppercase.
- `MIN_ALIAS_LEN = 4`.
- `load_aliases(path) -> list[AliasEntry]` — słownik aliasów z YAML (mapa z kluczem `aliases`;
  wpis: `alias`, `tickers`, opcjonalnie `ambiguous`/`exact_case`). Walidacja (D-030): kolizja
  aliasu (>1 ticker, w jednym lub między wpisami) wymaga `ambiguous: true` we wszystkich
  wpisach z tym aliasem; alias < 4 znaków wymaga `exact_case: true`. Brak pliku = pusty słownik;
  błędy schematu/walidacji → `ConfigError`.

### Konfiguracja (rozszerzenie S04)

- `UniverseCfg.min_history_sessions: int = 250` — minimum sesji historii (Część I sekcja 8);
  dodane z domyślną wartością, jawnie w `config/config.yaml` (D-027).
- Nowe pliki: `config/universe_seed.yaml` (skład WIG20/mWIG40 — dane do weryfikacji V-05),
  `config/aliases.yaml` (słownik aliasów dla S10).

### CLI (rozszerzenie S04)

- `gpwbot universe [--as-of DATA]` — najpierw sync seedu, potem wypisuje członków i powody
  wykluczeń; `--as-of`: data `YYYY-MM-DD` → koniec dnia sesji w Warszawie (23:59:59), ISO
  datetime bez strefy → Warszawa; brak opcji → bieżący czas. Kod 1 przy błędzie konfiguracji,
  kształtu `--as-of` lub bazy danych.

## S05

### `gpwbot.features`

- Funkcje wskaźników `Series → Series` (NaN przed pełnym oknem): `sma(s, n)`, `ema(s, n)`
  (`span=n`, `adjust=False`, `min_periods=n`), `rsi_wilder(close, n=14)`,
  `atr_wilder(high, low, close, n=14)`, `momentum(adj, n)` = `adj/adj.shift(n) − 1`,
  `momentum_12_1(adj)` = `shift(21)/shift(252) − 1`, `volume_zscore(v, n=60)` (std == 0 → NaN),
  `dist_to_high(close, n=252)` = `close/rolling_max − 1` (≤ 0), `avg_turnover(t, n=20)`,
  `realized_vol(adj, n=20)` = std logarytmicznych stóp zwrotu × √252. Wilder: pierwsza średnia
  to SMA z pierwszych `n` obserwacji, dalej `(prev×(n−1)+x)/n`; TR[0] = high − low; pierwszy RSI
  na indeksie `n`, pierwszy ATR na `n−1`; RSI: straty=0 i zysk>0 → 100, obie=0 → 50 (D-032/D-033).
- `FeatureSet` (pydantic, frozen): `ticker`, `as_of`, `close`, `ret_1d`, `mom_3m` (63 sesje),
  `mom_6m` (126), `mom_12_1`, `sma50`, `sma200`, `trend_ok` (close > sma200 i sma50 > sma200),
  `rsi14`, `atr14`, `atr_pct`, `vol_z`, `dist_52w_high`, `avg_turnover_20d`, `vol_ann`, `n_obs`,
  `stale`; ceny/kwoty jako `Decimal`, bezwymiarowe wskaźniki jako `float`; niewystarczająca
  historia → `None`.
- `compute_features(ticker, df, as_of, *, calendar) -> FeatureSet` — tnie do wierszy o dacie ≤
  `as_of` (brak look-ahead; deduplikacja i sortowanie defensywne); wskaźniki cenowe na
  `adj_close` (fallback `close`, gdy brak wartości), ATR na surowych OHLC, `stale` wg
  `quality.is_fresh` wobec kalendarza GPW — stąd wymagany keyword-only `calendar: GpwCalendar`
  (D-033). Naive `as_of` → `ValueError`; pusty df → wszystkie pola `None`, `n_obs=0`,
  `stale=True`.
- `save_features(session, fs) -> None` — upsert do `feature_snapshots` po `(instrument_id,
  as_of)`; instrument wyszukiwany po tickerze z `FeatureSet`; nieznany ticker → `ValueError`;
  payload JSON przez `model_dump(mode="json")` (kwoty jako tekst — D-032); flush bez commitu.

### `gpwbot.screener`

- `Reason` — `StrEnum`: `TOP_SCORE`, `POSITION`, `TEXT_EVENT` (wartości małe litery, wzorzec
  D-031).
- `ShortlistItem(ticker, reasons: tuple[Reason, ...], score)` / `Shortlist(items:
  tuple[ShortlistItem, ...])` — frozen dataclassy.
- `score_universe(features: Sequence[FeatureSet], cfg) -> dict[str, float]` — suma wag × rang
  dla pięciu cech (`mom_6m`, `mom_12_1`; `trend` jako 1/0; `low_vol` ← inwersja rangi
  `atr_pct`; `vol_z`); ranga percentylowa z uśrednionymi remisami, brak wartości = 0 (D-032);
  zduplikowany ticker → `ValueError`; pusta lista → `{}`.
- `build_shortlist(scores: Mapping[str, float], positions: Sequence[str], event_tickers:
  Mapping[str, float], cfg) -> Shortlist` — pozycje ∪ teksty (materiality ≥ próg; mapa
  ticker→materiality) ∪ top N po score; kolejność grup POSITION → TEXT_EVENT → TOP_SCORE
  (wewnątrz: (−score, ticker), dla tekstów (−materiality, ticker)); przy przekroczeniu
  `max_total` obcinanie najpierw TOP_SCORE od najniższego wyniku, potem TEXT_EVENT od najniższej
  istotności; pozycje nigdy nie są odcinane. Tickery normalizowane do uppercase; wynik
  deterministyczny i niezależny od kolejności wejścia.

### Konfiguracja (rozszerzenie S05)

- `ScreenerCfg` zyskuje wagi scoringu: `weight_mom_6m`, `weight_mom_12_1`, `weight_trend`,
  `weight_low_vol`, `weight_vol_z` (`float ≥ 0`, domyślnie po `1.0`; D-032); jawne wartości w
  `config/config.yaml`.

### CLI (rozszerzenie S05)

- `gpwbot screen [--as-of DATA]` — sync seedu → universe → features członków → scoring →
  wypisuje shortlistę z powodami (`score=… powody=top_score+…`); bez pozycji i eventów (brak
  ledgeru/tekstów do S06/S10). Kod 1 przy błędzie konfiguracji, kształtu `--as-of` lub bazy.

## S06

### `gpwbot.domain` (typy wspólne)

- `Side` — Enum: `BUY`, `SELL`; przeniesione z `gpwbot.costs` w S06 (D-034), tam reeksportowane
  (`from gpwbot.costs import Side` nadal działa).
- `OrderIntent(ticker, side, qty, limit_price=None, reason="")` — frozen dataclass; propozycja
  zlecenia przed zapisem do bazy (S07/S08).
- `FillRecord(order_id, session_date, price, qty, commission=Decimal("0"))` — frozen dataclass;
  dane realizacji (wejście do zapisu w `fills` i `PortfolioService.apply_fill`).

### `gpwbot.portfolio.fifo` (czysty)

- `Lot(id, buy_date, qty_remaining, unit_cost)` / `LotConsumption(lot_id, qty, unit_cost)` —
  frozen dataclassy; `unit_cost = cena + prowizja_zakupu/qty` w pełnej precyzji (D-035).
- `consume_lots(lots: Sequence[Lot], qty) -> list[LotConsumption]` — konsumpcja FIFO w kolejności
  `(buy_date, id)`; partie z zerowym stanem pomijane; `qty <= 0` → `ValueError`;
  `qty > suma partii` → `LedgerError`.

### `gpwbot.portfolio.service`

- `Position(ticker, qty, avg_cost, last_price, market_value, unrealized_pnl, held_sessions,
  stop_price)` — frozen dataclass; wartości jednostkowe w pełnej precyzji (D-035).
- `PortfolioService(session, clock, calendar: GpwCalendar, cfg: AppConfig)` — metody flushują bez
  commitu (transakcję prowadzi wywołujący); parametry rynkowe wyłącznie z `cfg` (A3):
  - `create_portfolio(pdef: PortfolioDef) -> int` — idempotentnie po nazwie; zapisuje wpłatę
    początkową jako wpis DEPOSIT (`available_from = NULL`; D-036).
  - `cash_total(pid) -> Decimal` — suma wszystkich wpisów ledgera (wraz z wpłatą).
  - `cash_available(pid, on_date: dt.date) -> Decimal` — wpisy z `available_from IS NULL OR <=
    on_date` minus aktywne rezerwacje (`orders.reserved_amount`).
  - `reserve(pid, order_id, amount)` / `release(order_id)` — blokada/zwolnienie gotówki pod
    zlecenie; nadpisanie dozwolone, release idempotentny; ujemna kwota → `ValueError`, cudze lub
    nieistniejące zlecenie oraz strona inna niż BUY → `LedgerError` (D-040).
  - `apply_fill(fill_id)` — BUY: nowa partia (`unit_cost = cena + prowizja/qty`), wpisy BUY i
    COMMISSION (wpływ od daty sesji), zwolnienie rezerwacji, `opened_session` przy nowej pozycji;
    SELL: konsumpcja FIFO, wpis SELL i COMMISSION (wpływ od daty rozliczenia T+N przez kalendarz,
    albo od razu przy `settle_instantly`; D-036), `fills.realized_pnl = przychód − koszt partii −
    prowizja`, `last_sell_session`. Po obu stronach: ujemna gotówka po księgowaniu → `LedgerError`
    (D-038); sprzedaż > stan → `LedgerError` (przez FIFO).
  - `positions(pid, prices: Mapping[str, Decimal]) -> list[Position]` — suma partii per ticker;
    brak ceny dla posiadanej pozycji → `ValueError`; `held_sessions` od `opened_session` do daty
    warszawskiej zegara.
  - `equity(pid, prices) -> Decimal` = `cash_total` + wartość pozycji (D-038).
  - `peak_equity(pid) -> Decimal | None` — maksimum z `portfolio_snapshots`; `None`, gdy brak.
  - `snapshot(pid, date, prices)` — upsert snapshotu (equity, cash, pozycje jako JSON z kwotami
    w tekście).
  - `apply_corporate_actions(pid, as_of: dt.date) -> int` — dywidendy i splity z `ex_date <=
    as_of`; idempotentne po `ref` (`DIV:<inst>:<ex>` / marker ADJUSTMENT o kwocie 0 dla
    `SPLIT:<inst>:<ex>`; D-037); dywidenda: data bytu = sesja przed `ex_date`, stan z replayu
    fills (ujemny stan → `LedgerError`), netto na sumie pozycji — jedno zaokrąglenie do 0.01
    (D-039) — wpływ od `pay_date`; split: partie kupione przed `ex_date` (×współczynnik, koszt ÷;
    walidacja wszystkich partii przed mutacjami), wynik ułamkowy → `LedgerError`. Zwraca liczbę
    zastosowanych akcji.
  - `check_invariants(pid)` — gotówka ≥ 0; partie > 0; każda sprzedaż ma `realized_pnl`;
    rezerwacje tylko dla BUY; naruszenie → `LedgerError` (D-038).

### Baza danych (rozszerzenie S01)

- Nowe tabele: `position_meta(portfolio_id, instrument_id [PK], stop_price, entry_atr,
  opened_session, last_sell_session)` — stop/ATR ustawia S08; `corporate_actions(instrument_id,
  type, ex_date [PK], pay_date, value)` (type: DIVIDEND/SPLIT).
- Nowe kolumny: `fills.realized_pnl` (NULL dla BUY), `cash_ledger.available_from` (data wpływu;
  NULL = natychmiast), `orders.reserved_amount` (aktywna rezerwacja; D-036).
- Rewizja Alembic `a3f5c8e2b9d4_s06_ledger_fifo`; semantyka `tax_lots.buy_price` = dokładny koszt
  jednostkowy (D-035).

### Testy / fixture

- `tests/portfolio/`: `test_fifo.py` (czysta logika), `test_service.py` (integracja z `tmp_db`:
  fills, rezerwacje, T+2/weekend/święto przez nadpisanie kalendarza, dywidendy, splity, snapshoty,
  izolacja portfeli), `test_properties.py` (`hypothesis`: losowe ciągi kupna/sprzedaży —
  niezmienniki po każdym kroku, tożsamość P/L z odchyleniem ≤ 0.01).

## S07

### `gpwbot.execution.orders` (enumy)

- `OrderType` — StrEnum: `LIMIT_AT_OPEN = "limit_at_open"` (jedyne typy v1; wartości małe litery,
  wzorzec D-031).
- `OrderStatus` — StrEnum: `PENDING`, `FILLED`, `EXPIRED`, `REJECTED`, `CANCELLED`.
- `OrderReason` — StrEnum (kody do `orders.reason`): `NO_CASH`, `NO_POSITION`, `LIMIT_NOT_MET`,
  `VOLUME_LIMIT`, `NO_BAR`, `BAD_DATA` (nieprawidłowe dane świecy/wiersza zlecenia; D-049).

### `gpwbot.execution.fill_model` (czysta logika)

- `FillResult(price, qty, commission, slippage_cost)` — frozen dataclass; kwoty `Decimal`; pełna
  ilość (bez częściowych realizacji).
- `simulate_fill(order: OrderIntent, bar: PriceBar, costs_cfg: CostsCfg, tick_table: TickTable,
  group="default") -> FillResult | None` — kolejność reguł: limit otwarcia (`LIMIT_NOT_MET`) →
  udział w wolumenie sesji (`VOLUME_LIMIT`; granica dozwolona, `volume=None` nie blokuje, ujemny
  wolumen przekracza limit; D-043/D-049) →
  cena = `apply_slippage(open, side, bps)` zaokrąglone do ticka (BUY UP / SELL DOWN), przycięte do
  limitu bez ponownego zaokrąglenia (D-044) → `commission` z `costs.commission`,
  `slippage_cost = abs(cena − open) × qty`. `qty ≤ 0` → `ValueError`.
- `rejection_reason(order, bar, costs_cfg) -> str | None` — powód braku filla (`LIMIT_NOT_MET` /
  `VOLUME_LIMIT`) albo `None`; współdzieli predykaty z `simulate_fill` (spójność; D-042).

### `gpwbot.execution.sim_broker`

- `SessionExecutionReport(session_date, filled_order_ids=(), expired_orders=())` — frozen
  dataclass; `filled_order_ids: tuple[int, ...]` w kolejności przetwarzania (najpierw SELL),
  `expired_orders: tuple[tuple[int, str], ...]` = `(order_id, kod powodu)`.
- `SimBroker(session, portfolio_service, calendar, tick_table, cfg, clock)` — metody flushują bez
  commitu (transakcję prowadzi wywołujący):
  - `submit(portfolio_id, run_id: int | None, intents: list[OrderIntent]) -> list[int]` — zlecenia
    PENDING z wspólnym `valid_from_session = calendar.next_session_after(clock.now())`; BUY
    rezerwuje `qty × limit + prowizja(qty × limit)` (brak środków → wiersz REJECTED/`NO_CASH`),
    SELL ponad stan (posiadane − już zlecone do sprzedaży) → REJECTED/`NO_POSITION`; odrzucone
    zostają w bazie dla audytu; nieznany ticker, `limit_price ≤ 0/None`, `qty ≤ 0` → `ValueError`
    (D-045). Zwraca id wszystkich utworzonych wierszy w kolejności wejścia.
  - `process_session(portfolio_id, session_date) -> SessionExecutionReport` — PENDING z
    `valid_from ≤ session_date` i `processed_session IS NULL`; najpierw SELL, potem BUY (jawny
    CASE, nie porównanie stringów), w obrębie typu rosnąco po `id`; świeca = pierwszy wiersz
    `prices_daily` per instrument (rosnąco po `source`; D-048); brak świecy → EXPIRED/`NO_BAR`,
    flaga jakości / open ≤ 0 / ujemny wolumen / wiersz niezgodny z LIMIT_AT_OPEN → EXPIRED/
    `BAD_DATA` (D-049), inaczej `simulate_fill` (grupa ticków `"default"`; resztkowe ValueError
    też → BAD_DATA); fill → wiersz `fills` + `apply_fill` (księguje i zwalnia rezerwację),
    wygaśnięcie → `release`; po każdym: `processed_session = session_date` — ponowne wywołanie
    no-op (D-047). Finansowanie kupna sprzedażą z tej samej sesji niemożliwe przy submit (D-050).

### Konfiguracja (rozszerzenie S02)

- `CostsCfg.max_volume_participation_pct: Decimal = 5` — maksymalny udział pojedynczego zlecenia w
  wolumenie sesji (S07, fill model); jawne w `config/config.yaml`; domyślna wartość do
  weryfikacji V-07 (D-041).

### Baza danych (rozszerzenie S06)

- Nowe kolumny: `orders.processed_session` (`Date`, NULL — sesja przetworzenia; idempotencja),
  `orders.reason` (`Text` — teza zlecenia albo kod powodu REJECTED/EXPIRED; D-046).
- Rewizja Alembic `9b4d6e1c5a83_s07_execution_simulator`.

### Testy / fixture

- `tests/execution/`: `test_fill_model.py` (tabela ≥ 12 przypadków czystej logiki + spójność
  `rejection_reason`, w tym ujemny wolumen → VOLUME_LIMIT), `test_sim_broker.py` (submit:
  rezerwacja, NO_CASH, SELL ponad stan, pusta lista, limit None/0/ujemny,
  `valid_from_session` przed otwarciem/w trakcie/piątek wieczorem; process_session: kolejność
  SELL→BUY, `settle_instantly` finansuje zakup vs T+2, przypięcie D-050 (finansowanie w sesji
  niemożliwe), NO_BAR, BAD_DATA (flaga jakości/open ≤ 0/ujemny wolumen/SELL poniżej ticka/wiersz
  niezgodny z LIMIT_AT_OPEN), wygasanie z kodami, zwolnienie rezerwacji, idempotencja, czekanie na
  sesję; scenariusz demonstracyjny kupno/wygaśnięcie/sprzedaż), `test_sim_broker_properties.py`
  (`hypothesis`: losowe zlecenia i ceny — `check_invariants` po każdym kroku).
