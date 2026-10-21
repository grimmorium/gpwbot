# Decyzje i weryfikacje (sekcja 18 Części I)

Format: `D-xxx` — decyzja przy niejednoznaczności specyfikacji lub wybór implementacyjny.
Weryfikacje z sekcji 18 są przypisane do sesji, która je domyka; agent NIE zgaduje.

## Decyzje S00

### D-001. Placeholdery sekcji `screener`, `baseline`, `text`, `eval` w konfiguracji
Specyfikacja wymaga tych pól w `AppConfig` (S00), ale pola szczegółowe definiują dopiero sesje
S05/S09/S10–S12/S14. W S00 zdefiniowano minimalne modele z wartościami domyślnymi zgodnymi z
Częścią I: `screener(top_n=25, max_total=40, materiality_threshold=0.6)`,
`baseline(top_k=10, rebalance_sessions=5)`, `text(dedupe_similarity=92, extraction_temperature=0.0)`,
`eval(min_forward_days=90)`. Kolejne sesje rozszerzają modele i aktualizują INTERFACES.md.

### D-002. Nadpisywania ze środowiska `GPWBOT__SEKCJA__KLUCZ`
Obsługiwane są wyłącznie sekcje pierwszego poziomu; nazwy sekcji/kluczy porównywane bez
względu na wielkość liter (klucze z podkreśleniami, np. `base_url`, jako `BASE_URL`).
Typ wartości nadpisywania rzutuje pydantic przy walidacji. Walidacja celu odbywa się
względem schematu `AppConfig` (nie pliku YAML): sekcja nieobecna w pliku może zostać
utworzona, jeśli jest znana schematowi; zmienna o złym kształcie (brak drugiego `__`)
lub wskazująca nieznany schematowi klucz — rzuca `ConfigError` (spójne z `extra=forbid`).

### D-003. Ścieżki w konfiguracji
Pola `PathsCfg` są wymagane (brak domyślnych) — jawnieść ponad wygodę; błędny config ma być
widoczny od razu przy `doctor`. Ścieżki względne rozwiązywane względem katalogu pliku YAML.

### D-004. Kody wyjścia CLI
`0` = sukces, `1` = błąd konfiguracji (`doctor`), `2` = komenda nie zaimplementowana (stuby).
Umożliwia to skrypty operacyjne bez parsowania tekstu.

### D-005. Logowanie w S00: structlog standalone
W S00 konfigurowany jest sam structlog (`PrintLoggerFactory`), bez mostka do stdlib; integracja
z logami bibliotek (httpx, sqlalchemy) dojdzie wraz z sesjami I/O (S01+). `bind_run` używa
własnego `ContextVar` + procesora wstrzykującego `run_id` (wymóg A3: contextvars).

### D-006. Uruchamianie kontroli jakości na Windows
Docelowe środowisko to Linux; `scripts/check.sh` jest kanoniczny i wykrywa oba układy venv
(`.venv/bin/python`, `.venv/Scripts/python.exe`), więc działa też pod git-bash na Windows.
Lustrzane `scripts/check.ps1` (dodatkowy plik poza listą S00) zostaje jako alternatywa, gdyby
brakowało jakiegokolwiek basha.

### D-007. Kontrola pokrycia w check.sh
Pokrycie mierzy `pytest --cov=gpwbot --cov-report=json`; progi egzekwuje
`scripts/check_coverage.py`: moduły logiki (features, risk/, fifo, fill_model, metrics,
calendar, ticks) ≥ 90%, reszta ≥ 75% (A4). W S00 lista modułów logiki jest pusta — progi
dotyczą istniejących plików.

### D-008. `split_spec.py` — granice sekcji
Sekcja trwa od nagłówka `### Sdd` do następnego nagłówka pasującego do `^###\s+S` (dowolna
sesja) lub do nagłówka poziomu 2 (`## ...`). Treść pliku zawiera własny nagłówek. Brak sekcji
nie jest błędem (pusta lista).

## Decyzje S01

### D-009. `upsert_prices(..., now=...)` — czas `ingested_at`
Specyfikacja podaje sygnaturę `upsert_prices(session, instrument_id, bars, source)`, ale A3
zakazuje `datetime.now()` poza `SystemClock`. Dodano opcjonalny parametr keyword-only
`now: dt.datetime | None = None`: domyślnie `SystemClock().now()`, testy i wywołania z
wstrzykiwanym zegarem przekazują jawną wartość. Sygnatura pozycyjna specyfikacji nie uległa
zmianie.

### D-010. Autogeneracja rewizji a typy własne (`DecimalStr`/`UtcDateTime`)
Alembic renderuje typy spoza `sqlalchemy.*` jako w pełni kwalifikowane nazwy
(`gpwbot.db.types.UtcDateTime()`) i NIE emituje dla nich importów. W wygenerowanej rewizji
dodano ręcznie `import gpwbot.db.types`. Każda przyszła autogeneracja używająca typów własnych
wymaga tej samej poprawki (albo zastąpienia w DDL przez `sa.String()`/`sa.DateTime()`, co jest
równoważne, bo TypeDecorator kompiluje się do typu impl).

### D-011. Ruff pomija katalog wygenerowanych rewizji
`extend-exclude = ["src/gpwbot/db/migrations/versions"]` w `pyproject.toml`: styl kodu
autogenerowanego przez Alembic nie podlega regułom projektu (E501, UP007 itd.). Pliki ręczne
w `migrations/` (`env.py`, `script.py.mako`) są lintowane normalnie.

### D-012. `pandas-stubs` jako zależność dev
`repo.get_prices_df` zwraca DataFrame; aby utrzymać `mypy --strict` bez wyłączeń, dodano
`pandas-stubs` do `[dev]` (analogicznie do istniejącego `types-PyYAML`).

## Decyzje S02

### D-013. "Otwarcie sesji" = początek notowań ciągłych
Test DST z S02 (otwarcie 2026-03-27 = 08:00 UTC, 2026-03-30 = 07:00 UTC) jednoznacznie
określa semantykę: `session_open_ts(d)` to początek notowań ciągłych (9:00 lokalnie), a nie
start fazy przed otwarciem. Faza przed otwarciem to wyłącznie składanie zleceń — transakcje
są zawierane od 9:00 (SZOG §5–§6). `session_close_ts(d)` = koniec aukcji zamknięcia (17:00);
faza dogrywki 17:00–17:05 nie jest modelowana w v1.

### D-014. Godziny sesji GPW zweryfikowane — korekta `preopen_start` na 8:30
Zgodnie z SZOG §5(1)(1) (PDF: gpw.pl/pub/GPW/files/PDF/regulacje/SZOG_GPW_WATS_pl.pdf,
dostęp 2026-10-16): aukcja otwarcia **8:30–9:00**, notowania ciągłe 9:00–16:50, aukcja
zamknięcia 16:50–17:00. Przykładowa wartość `preopen_start: "08:00"` z sekcji 2 Części I
była błędna — skorygowano w `config/config.yaml` i domyślnych wartościach `MarketCfg`.
Dni wolne GPW 2025–2026 (gpw.pl "Szczegóły sesji", dostęp 2026-10-16) są zgodne z XWAR
exchange_calendars 4.13.2 bez wyjątków — `config/gpw_holidays_override.yaml` jest pusty;
GPW nie honoruje "przeniesionych" dni wolnych (5 maja i 3 listopada 2025 to sesje).

### D-015. Model tick size: grupy pasm cenowych, wartości przykładowe
SZOG §70: krok notowania akcji jest ustalany per instrument wg pasma płynności z tabeli
załącznika do Rozporządzenia delegowanego (UE) 2017/588 i ceny w zleceniu; nie może być
mniejszy niż wartość z tej tabeli. Model S02 upraszcza to do statycznych grup pasm cenowych
(`config/tick_sizes.yaml`). **Wartości tabeli są przykładowe** — EUR-Lex był niedostępny
w trakcie S02, więc V-02 pozostaje otwarte; tabela jest zaprojektowana tak, by tick każdego
wyższego pasma był wielokrotnością niższych (granice 10/100 są wielokrotnościami ticków),
co gwarantuje idempotencję `round_price` i ważność wyniku w jego własnym paśmie.

### D-016. `commission(0) = 0`, nie minimum
Wzór specyfikacji `max(value × pct, min)` dla wartości 0 dałby prowizję minimalną, co jest
nieuzasadnione biznesowo (brak transakcji → brak prowizji). Zaimplementowano wyjątek:
`value == 0 → Decimal("0.00")`; ujemna wartość rzuca `ValueError`.

### D-017. Ścieżka pliku nadpisań kalendarza
`GpwCalendar(cfg, override_path=None)`: przy `None` domyślnie `config/gpw_holidays_override.yaml`
względem CWD (CLI działa z roota projektu); testy przekazują jawną ścieżkę. Brak pliku = puste
nadpisywania (nie błąd). Format: mapa z kluczami `holidays`/`sessions` (listy dat ISO); pokrycie
dat między listami rzuca `ConfigError`.

### D-018. Konwencje arytmetyki sesji
`sessions_between(a, b)` i `count_sessions(a, b)` liczą przedział półotwarty `(a, b]` — dzięki
temu `add_sessions`/`count_sessions` są wzajemnie odwrotne (wymóg testów S02).
`add_sessions(d, n)` wymaga, by `d` był dniem sesji (`ValueError` w przeciwnym razie);
`previous_session(d)` działa dla dowolnego dnia.

### D-019. `CostsCfg.dividend_tax_pct = 19`
Sekcja 2 Części I wymienia podatek 19% jako parametr rynku, ale S00 nie miał pola na niego.
Dodano `dividend_tax_pct: Pct = Decimal("19")` do `CostsCfg` (dodanie z domyślną wartością —
bez zmiany istniejących sygnatur); `net_dividend(gross, tax_rate)` przyjmuje stawkę jawnie,
a S07/S09 będą ją czytać z konfiguracji.

### D-020. Mypy: `ignore_missing_imports` dla exchange_calendars
Biblioteka nie ma stubów ani markera `py.typed`; dodano override per-moduł w `pyproject.toml`
(analogicznie do podejścia z `pandas-stubs`, D-012 — tu bez pakietu stubów, bo API używane
w S02 jest wąskie).

## Decyzje S03

### D-021. `AppContext` zyskuje pole `session_factory` (zmiana sygnatury)
Planowane w PROGRESS.md od S00 ("naturalny moment: S03"). `build_context` tworzy fabrykę
domyślnie (`create_engine_for` + `make_session_factory`) i akceptuje override
`session_factory`. Jedyna konstrukcja bezpośrednia `AppContext(...)` była w samym
`context.py`; testy korzystają z `build_context`, więc nic się nie łamie. Migracje NIE są
stosowane przy budowie kontekstu — baza musi być gotowa (`gpwbot db upgrade`).

### D-022. `providers.base.PriceBar` ma dodatkowe pole `turnover_pln`
Specyfikacja S03 wymienia 7 pól baru źródłowego, ale wymaga też użycia obrotu podanego
przez źródło ("jeśli źródło go nie podaje" → liczenie `close × volume`). Bez pola w barze
dane z kolumny Turnover byłyby tracone. Pole opcjonalne (`None`), domyślne — sygnatura
specyfikacji zachowana (wszystkie wymienione pola, w tej kolejności).

### D-023. Próg skoku dziennego 25% jako stała `quality.LARGE_CHANGE_PCT`
Specyfikacja S03/Część I sekcja 7 podaje wartość jawnie ("dzienna zmiana > 25%") — traktuję
ją jako część algorytmu, nie parametr rynku (w odróżnieniu od limitów operacyjnych: limiter,
retry, timeout → konfiguracja). Przeniesienie do config możliwe bez zmiany sygnatur
(`check_bars` ma stałą domyślną w module).

### D-024. Parser Stooq: nagłówki PL/EN i przecinek dziesiętny (heurystyka)
Nagłówki mapowane przez tablicę aliasów (case-insensitive): EN `date/open/high/low/close/
volume/turnover` + polskie odpowiedniki (`data/otwarcie/maksimum/minimum/zamknięcie/wolumen/
obrót`). Przecinek jako separator dziesiętny obsługiwany dwojako: wartości zacytowane
(`"98,50"`) przez `csv` + zamianę `,`→`.`; niezacytowane (wartość zajmuje kilka komórek) —
łączeniem par czysto cyfrowych od lewej do dopasowania liczby kolumn. **Rzeczywisty kształt
odpowiedzi Stooq pozostaje do weryfikacji (V-03)**; test `@network` służy do jej sprawdzenia.

### D-025. Retry i operacyjne detale providera Stooq
Ponawiane wyłącznie 5xx i timeouty (`tenacity`, wykładniczy backoff, mnożnik z configu);
4xx → natychmiast `ProviderError` bez ponawiania; inne błędy httpx (np. `ConnectError`) —
bez ponawiania wg specyfikacji. Po wyczerpaniu prób: `ProviderError("… N nieudanych prób …")`.
Limiter zapytań to klasa `RateLimiter` (monotoniczny zegar, `time.sleep`). Ścieżka mapy
symboli domyślnie `config/symbol_map.yaml` względem CWD (wzorzec D-017); testy i CLI przekazują
jawnie albo działają z roota projektu.

### D-026. Lista tickerów cen przed S04: `default_price_tickers` = indeksy ∪ benchmarki
Sesja S03 nie ma jeszcze modułu universe (S04), więc CLI (`ingest --prices`,
`backfill-prices`) i domyślny `backfill_prices(ctx, years)` pobierają tickery z konfiguracji:
`universe.indices ∪ portfolio.benchmarks`. Po S04 lista będzie pochodzić z rozszerzenia
universe — funkcja do usunięcia/wymiany.

## Decyzje S04

### D-027. `UniverseCfg.min_history_sessions = 250` (nowe pole konfiguracji)
Część I sekcja 8 wymaga filtra "co najmniej 250 sesji historii", a zasady sesji nakazują, by
limity pochodziły z konfiguracji, nie z kodu. Dodano `min_history_sessions: int = Field(
default=250, gt=0)` do `UniverseCfg` (dodanie z domyślną wartością — bez zmiany istniejących
sygnatur; wzorzec D-019) i jawnie w `config/config.yaml`.

### D-028. Wykrywanie SUSPENDED ze świec dziennych (heurystyka, V-06)
W v1 nie ma oficjalnego źródła zawieszeń notowań GPW, a model `Instrument` nie ma pola
`suspended`, więc zawieszenie wykrywamy z danych cenowych: **ostatnia świeca (≤ as_of) ma
`volume == 0` i `turnover_pln ∈ {None, ≤ 0}`**. Świeca z `volume = NULL` NIE oznacza
zawieszenia (brak danych ≠ brak obrotów). Reguła sprawdzana przed STALE — świeca zerowa jest
bardziej specyficznym sygnałem niż wiek danych. Do weryfikacji: oficjalne źródło zawieszeń
(V-06); heurystyka do ewentualnej wymiany bez zmiany sygnatur `get_universe`.

### D-029. Kolejność filtrów i przypadki brzegowe `get_universe`
Pierwszy trafiony powód wygrywa: INACTIVE → SUSPENDED → STALE → SHORT_HISTORY → LOW_TURNOVER
→ LOW_PRICE → NEEDS_REVIEW (kolejność w docstringu funkcji). Skutki:
- brak jakichkolwiek cen ≤ as_of = `SHORT_HISTORY` (zero historii, nie STALE);
- obrót 20-sesyjny = średnia z ostatnich 20 dostępnych barów (daty ≤ as_of), dni bez obrotu
  liczone jako 0; porównanie w `Decimal` (granica == próg przechodzi);
- przy wielu źródłach cen na tę samą datę liczony jest pierwszy wiersz po dacie
  (w praktyce jedno źródło do czasu S15 — patrz dług techniczny);
- `cfg.universe.exclude_tickers` nie są kandydatami w ogóle (nie pojawiają się ani w
  `members`, ani w `excluded`) — specyfikacja zamyka zbiór powodów wykluczenia i nie ma
  wartości "wykluczono ręcznie"; decyzja widoczna w samym pliku konfiguracji.

### D-030. Schemat i walidacja `config/aliases.yaml`
Wpis: `alias` (str) + `tickers` (niepusta lista str, normalizowane do uppercase) + opcjonalne
`ambiguous`/`exact_case` (bool). Reguły: alias krótszy niż 4 znaki wymaga `exact_case: true`;
ten sam alias (porównanie case-insensitive po trimie) przypisany do >1 tickera — w jednym
wpisie lub między wpisami — wymaga `ambiguous: true` **we wszystkich** wpisach z tym aliasem
(strict; jeden nieoznaczony wpis = błąd). Brak pliku = pusty słownik (wzorzec D-025); nieznane
klucze i błędy schematu → `ConfigError`. Seed (`universe_seed.yaml`) ma osobny, prostszy
schemat: lista map z kluczami `ticker/isin/name/segment/sector/indices/tick_group/aliases`;
brak pliku seedu = błąd (sync z pustym seedem dezaktwyzowałby całą bazę).

### D-031. `ExclusionReason` jako `StrEnum` (ruff UP042)
Klasa dziedzicząca po `(str, Enum)` jest flagowana przez ruff (UP042); projekt wymaga Pythona
3.11+, więc użyto `enum.StrEnum`. Semantyka zachowana: wartości to te same małe ciągi,
porównania z literałami i `.value` działają identycznie; CLI wypisuje wyłącznie `.value`,
więc różnice w formatowaniu członków nie wpływają na wyjście.

### D-032. Screener: skala rangi, mapowanie cech i wagi scoringu
- Ranga percentylowa = uśredniona pozycja (1-based) wśród ważnych wartości / liczba ważnych →
  przedział `[1/k, 1]`; brak wartości (`None`/NaN) = ranga `0.0`; remisy dostają tę samą
  uśrednioną rangę; wynik nie zależy od kolejności wejścia (suma per ticker ma stały porządek
  składników).
- `trend` jako dosłowne `1` lub `0` (nie percentyl); cecha `low_vol` ← inwersja rangi
  `atr_pct` (Część I sekcja 8: "zmienność (ATR14 / cena)"); `vol_z` — wyższy = lepiej.
- Wagi w `ScreenerCfg` (`weight_mom_6m`, `weight_mom_12_1`, `weight_trend`, `weight_low_vol`,
  `weight_vol_z`) mają domyślnie po `1.0` — wartości neutralne i arbitralne; kalibracja wag
  jest poza zakresem S05 (parametry wyłącznie w konfiguracji).
- `features_json` w `feature_snapshots`: kwoty jako tekst, daty jako ISO (pydantic
  `model_dump(mode="json")`) — spójnie z konwencją A3 "w bazie tekst przez DecimalStr".

### D-033. `compute_features`: wymagany kalendarz, cena surowa w FeatureSet, ATR na OHLC
- Sygnatura specyfikacji to `compute_features(ticker, df, as_of)`; dodano keyword-only
  parametr `calendar: GpwCalendar` — flaga `stale` jest definiowana wobec ostatniej
  zakończonej sesji GPW (`quality.is_fresh`, godziny faz z konfiguracji), więc bez kalendarza
  nie da się jej obliczyć poprawnie. Wszyscy wywołujący produkcyjni (CLI, S09) mają
  `ctx.config`.
- `FeatureSet.close` = surowa cena zamknięcia ostatniej świecy (cena transakcyjna);
  `trend_ok` porównuje ją ze `sma50`/`sma200` liczonymi na serii skorygowanej.
- ATR liczone na **surowych** OHLC: służy poziomom stop-loss (S08), więc musi być
  porównywalne z aktualną ceną transakcyjną; istnieje tylko `adj_close`, a mieszanie skal
  zniekształciłoby TR.
- Konwencja Wildera: `TR[0] = high − low` (brak prev_close); pierwszy RSI na indeksie `n`
  (delt od drugiej ceny), pierwszy ATR na indeksie `n−1`.

## Weryfikacje z sekcji 18 Części I (do domknięcia w sesjach)

| # | Kwestia | Odpowiedzialna sesja | Status |
| --- | --- | --- | --- |
| V-01 | Aktualne godziny sesji GPW, widełki wahań, T+2 vs T+1 (regulaminy GPW/KDPW) | S02 | częściowo domknięte — godziny zweryfikowane wg SZOG §5(1)(1), `preopen_start` skorygowane na 8:30 (D-014); widełki statyczne WIG20/mWIG40/inne = ±30/±15/±10% od kursu odniesienia wg przedziału ceny (SZOG §71) — płaska wartość `price_limit_pct: 10` w config to uproszczenie do walidacji limitów; **T+2 vs T+1 pozostaje otwarte** |
| V-02 | Tabela tick size (źródło: regulamin GPW) | S02 | częściowo domknięte — mechanizm zweryfikowany wg SZOG §70 (pasma płynności UE 2017/588 + cena; D-015); **wartości tabeli pozostają przykładowe** (EUR-Lex niedostępny w trakcie S02) |
| V-03 | Dostępność, format i warunki użycia Stooq / yfinance / RSS / ESPI; czy ceny są skorygowane | S03 | częściowo domknięte — mechanizm zaimplementowany (parser PL/EN + przecinek dziesiętny, retry, limiter; D-024/D-025); **wzorzec URL i symbole indeksów niezweryfikowane** (brak dostępu do sieci w sesji) — test `@network` (`pytest -m network tests/providers/`) służy do domknięcia; warunki użycia Stooq/yfinance do potwierdzenia |
| V-04 | Która flaga llama.cpp steruje trybem rozumowania Qwen (`enable_thinking`?) | S11 | otwarte — config ma `thinking_extraction`/`thinking_decision` |
| V-05 | Skład WIG20/mWIG40 w `config/universe_seed.yaml` (tickery, nazwy, sektory) | S04 | częściowo domknięte — tickery WIG20 wg sekcji "Mapa wartości spółek z WIG20" na gpw.pl (dostęp 2026-10-20); mWIG40: nazwy wg pl.wikipedia.org, tickery = mapowanie agenta; część symboli WIG20 odbiega od znanych mi nazw (TAURONPE, MODIVO, KETY, ALIOR) — do weryfikacji przed startem forward testu |
| V-06 | Oficjalne źródło zawieszeń notowań GPW vs heurystyka zerowej świecy (D-028) | S04 | otwarte — v1 wykrywa SUSPENDED z `volume == 0` i braku obrotu na ostatniej świecy; do podpięcia oficjalnego źródła, gdy będzie dostępne |
