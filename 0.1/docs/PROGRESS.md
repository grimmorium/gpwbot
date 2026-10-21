# PROGRESS — gpwbot 0.1

Statusy: `todo` / `in-progress` / `done`. Aktualizowany na końcu każdej sesji (A1.4).

## Tabela sesji

| Sesja | Zakres | Status | Uwagi |
| --- | --- | --- | --- |
| S00 | Szkielet, konfiguracja, narzędzia | done | pyproject, config/clock/context/logging/cli, check.sh(+ps1), split_spec → docs/sessions/; 53 testów zielone, pokrycie 98%, `gpwbot doctor` działa |
| S01 | Baza danych i migracje | done | db/{types,models,session,repo,migrate}.py + Alembic (rewizja initial), CLI `db upgrade`, fixture `tmp_db`; 97 testów zielone, pokrycie 98% (moduły db ≥ 92%) |
| S02 | Kalendarz GPW, tick size, koszty | done | calendar/ticks/costs + config/{tick_sizes,gpw_holidays_override}.yaml; 190 testów zielone (93 nowe), pokrycie modułów ≥ 95%; godziny sesji zweryfikowane wg SZOG (preopen 8:30); ticki przykładowe (V-02) |
| S03 | Ceny: Stooq, jakość danych, ingest | done | providers/{base,prices_stooq,prices_yfinance}, quality, ingest/prices_job + config/symbol_map.yaml; 279 testów zielone (89 nowych), pokrycie modułów ≥ 90%; retry/limiter z configu; V-03 częściowo domknięte (wzorzec URL do weryfikacji — test @network) |
| S04 | Universe, instrumenty, aliasy | done | universe.py (sync seedu + filtry), aliases.py (walidacja), config/{universe_seed,aliases}.yaml, CLI `universe --as-of`; 312 testów zielone (33 nowe); seed WIG20/mWIG40 do weryfikacji (V-05); SUSPENDED = heurystyka zerowej świecy (D-028/V-06) |
| S05 | Wskaźniki i screener | done | features.py (wskaźniki + FeatureSet + save_features), screener.py (scoring przekrojowy + shortlista), wagi ScreenerCfg, CLI `screen`; 374 testy zielone (+62 nowe); pokrycie: features 93.5%, screener 100% |
| S06 | Ledger, pozycje, partie FIFO | done | domain.py (Side/OrderIntent/FillRecord), portfolio/{fifo,service}.py (PortfolioService: gotówka, rezerwacje, fills FIFO, T+2, dywidendy, splity, snapshoty, niezmienniki), migracja a3f5c8e2b9d4; 397 testów zielonych (+23 nowe); pokrycie fifo/service ≥ 90% |
| S07 | Symulator wykonania | todo | zależy od: S02, S06 |
| S08 | Risk Manager | todo | zależy od: S05, S06 |
| S09 | Strategia bazowa, runner, raport dzienny | todo | zależy od: S05–S08 |
| S10 | Teksty: RSS, ESPI, dedupe, linker | todo | zależy od: S01, S04 |
| S11 | Klient LLM, schematy, ekstrakcja | todo | zależy od: S10 |
| S12 | Kontekst i decider | todo | zależy od: S09, S11 |
| S13 | Pełna orkiestracja, replay, scheduler | todo | zależy od: S09, S12 |
| S14 | Ewaluacja i raporty tygodniowe | todo | zależy od: S09 |
| S15 | Operacyjność, powiadomienia, domknięcie | todo | zależy od: wszystkie |

Kamienie milowe: po S09 paper trading strategii bazowej (bez LLM); po S13 pełny agent; po S15 start forward testu.

## Decyzje

Pełna lista w `docs/decisions.md`. Z S00: D-001…D-008. Z S01: D-009 (parametr `now` w
`upsert_prices`), D-010 (autogenerate nie emituje importów typów własnych — ręczny import w
rewizji), D-011 (ruff pomija `migrations/versions`), D-012 (`pandas-stubs` jako zależność dev).
Z S02: D-013 ("otwarcie sesji" = start notowań ciągłych), D-014 (godziny GPW zweryfikowane wg
SZOG — `preopen_start` skorygowane na 8:30; dni wolne 2025–2026 zgodne z XWAR, override pusty),
D-015 (model ticków = grupy pasm cenowych; wartości przykładowe, V-02 otwarte), D-016
(`commission(0) = 0`), D-017 (ścieżka pliku nadpisań kalendarza), D-018 (przedział `(a, b]`;
`add_sessions` wymaga sesji), D-019 (`CostsCfg.dividend_tax_pct = 19`), D-020 (mypy:
`ignore_missing_imports` dla exchange_calendars). Z S03: D-021 (`AppContext.session_factory`;
zmiana sygnatury, planowana od S00), D-022 (`PriceBar.turnover_pln` poza listą pól specyfikacji),
D-023 (próg 25% jako stała `quality.LARGE_CHANGE_PCT`), D-024 (parser: nagłówki PL/EN + heurystyka
przecinka dziesiętnego; format Stooq do weryfikacji V-03), D-025 (retry 5xx/timeouty, 4xx bez
ponawiania; ścieżka mapy symboli względem CWD), D-026 (`default_price_tickers` = indeksy ∪ benchmarki
przed S04). Z S04: D-027 (`UniverseCfg.min_history_sessions = 250` — nowe pole konfiguracji),
D-028 (SUSPENDED = heurystyka zerowej świecy; V-06 otwarte), D-029 (kolejność filtrów
`get_universe`; brak cen = SHORT_HISTORY; `exclude_tickers` poza kandydatami), D-030 (schemat i
walidacja `aliases.yaml` oraz seedu), D-031 (`ExclusionReason` jako `StrEnum`, ruff UP042).
Z S05: D-032 (skala rangi percentylowej `[1/k, 1]`, brak wartości = 0; `low_vol` ← inwersja
`atr_pct`; wagi scoringu domyślnie po 1.0 — kalibracja poza S05), D-033 (`compute_features`
wymaga keyword-only `calendar` — stale wg `is_fresh`; `FeatureSet.close` = surowa cena; ATR na
surowych OHLC). Z S06: D-034 (`Side` w `domain`, reeksport w `costs`), D-035 (`tax_lots.buy_price`
= dokładny koszt jednostkowy bez kwantyzacji — tożsamość P/L), D-036 (wpłata początkowa jako wpis
DEPOSIT; `available_from` na każdym wpisie; rezerwacje w `orders.reserved_amount`), D-037
(idempotencja zdarzeń korporacyjnych po `ref`; marker ADJUSTMENT o kwocie 0 dla splitów; data bytu
dywidendy z replayu fills), D-038 (strażnik ujemnej gotówki w `apply_fill`; `positions` wymaga cen
wszystkich pozycji; equity z całej gotówki).

## Dług techniczny

- Sekcje `baseline`, `text`, `eval` w konfiguracji to placeholdery — rozbudowa w S09/
  S10–S12/S14 (D-001); sekcja `screener` rozszerzona o wagi scoringu w S05.
- Brak źródła danych dla tabeli `corporate_actions` (dywidendy/splity): wiersze tworzone są
  ręcznie lub przez przyszły adapter; `apply_corporate_actions` działa tylko na tym, co jest w
  bazie. Do podpięcia przed startem forward testu (S15 albo wcześniej).
- CLI `screen` bez pozycji i eventów (S05): shortlista = TOP_SCORE, bo brak ledgeru (S06)
  i tekstów (S10); podpiąć `positions`/`event_tickers` przy orkiestracji runu (S09/S13).
- Wagi screenera domyślnie po 1.0 (D-032) — kalibracja przed startem forward testu /
  przy ewaluacji (S14).
- Wzorzec URL Stooq i symbole indeksów (`config/symbol_map.yaml`) niezweryfikowane na żywej
  odpowiedzi (V-03) — domknięcie: `pytest -m network tests/providers/` + potwierdzenie warunków
  użycia. Heurystyka przecinka dziesiętnego (D-024) do uproszczenia po weryfikacji formatu.
- Adapter yfinance nie jest podpięty jako automatyczny fallback ingestu (S03: samodzielny
  adapter za flagą); decyzja o fallbackie — przy S15/operacyjności albo wcześniej, gdy Stooq
  okaże się niedostępny.
- `default_price_tickers` (indeksy ∪ benchmarki; D-026) nie został podpięty do tickerów z
  `config/universe_seed.yaml` — S04 zostawił sygnaturę i testy S03 nietknięte. Następny krok:
  wymiana na tickery seedu (z fallbackiem), zanim start forward testu; bez tego ingest/backfill
  pobiera tylko indeksy, a universe nie ma danych cen spółek.
- `sync_instruments` dezaktwyzuje też wiersze instrumentów spoza seedu tworzone przez ingest
  (np. indeksy WIG/WIG20) — celowe wg specyfikacji ("usunięty z seedu = active=false"), ale do
  przejrzenia, gdy pojawią się inne źródła wierszy `instruments`.
- CLI `ingest` bez `--prices` to nadal stub (teksty — S10); kody wyjścia: częściowa awaria
  tickerów nie zmienia kodu 0 (widoczne w raporcie na stdout).
- `upsert_instruments` robi select+update per ticker (nie batch upsert); przy dziesiątkach
  spółek w S04 to wystarcza, ale backfill może chcieć wersję wsadową.
- Tabela ticków w `config/tick_sizes.yaml` ma wartości przykładowe — do zastąpienia danymi z
  tabeli załącznika UE 2017/588 po domknięciu V-02 (D-015).
- Faza dogrywki GPW 17:00–17:05 nie jest modelowana w `SessionTimes` (S02, D-013) — doprecyzować
  przy fill model (S07), jeśli będzie miała znaczenie dla realizacji.
- Domyślna ścieżka nadpisań kalendarza jest względem CWD (D-017); po wprowadzeniu pola w
  `AppContext`/`PathsCfg` warto ją przenieść do konfiguracji jawnie.

## Pytania otwarte

- V-05: skład indeksów w `config/universe_seed.yaml` — tickery WIG20 pobrane z sekcji "Mapa
  wartości spółek z WIG20" na gpw.pl (dostęp 2026-10-20), ale część symboli odbiega od znanych mi
  nazw (TAURONPE, MODIVO, KETY, ALIOR — możliwe zmiany tickerów po 2025 lub błąd ekstrakcji);
  mWIG40: tickery to mapowanie agenta z listy nazw (pl.wikipedia.org), 9 spółek pominiętych
  (brak pewnego tickera). Proszę o weryfikację składu przed startem forward testu.
- V-06: czy heurystyka SUSPENDED (ostatnia świeca volume=0 i bez obrotu; D-028) jest wystarczająca,
  czy podpiąć oficjalne źródło zawieszeń notowań GPW?
- Podłączenie ingest/backfill do tickerów z seedu (dług techniczny, D-026): zrobić teraz jako
  osobny krok operacyjny, czy zostawić przed startem forward testu?
- V-03 (reszta): wzorzec URL Stooq (`https://stooq.pl/q/d/l/?s=…&i=d&d1=…&d2=…`) i symbole indeksów
  w `config/symbol_map.yaml` są do weryfikacji na żywej odpowiedzi — brak dostępu do sieci w trakcie
  S03. Proszę o uruchomienie `pytest -m network tests/providers/` (jedno zapytanie dla PKN) lub
  potwierdzenie warunków użycia Stooq/yfinance.
- V-02: wartości tabeli tick size — EUR-Lex był niedostępny w trakcie S02; mechanizm zweryfikowany
  (SZOG §70), liczby nie. Proszę o potwierdzenie źródła lub dostęp do tabeli UE 2017/588.
- V-01 (reszta): status T+2 vs T+1 w KDPW — config ma `settlement_days: 2`; w Europie planowane
  przejście na T+1, ale nie zweryfikowałem aktualnego stanu.
