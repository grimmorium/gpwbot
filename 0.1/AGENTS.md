# AGENTS.md — gpwbot (sesja S00)

Aplikacja Python: analiza spółek GPW i rekomendacje KUP/SPRZEDAJ/TRZYMAJ przez lokalny LLM,
paper trading z pełnym audytem. Pełna specyfikacja: `docs/SPEC.md` (Część I — biznes, Część II — technika).

## Komendy

```bash
# środowisko (raz)
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\...

# kontrola jakości (koniec KAŻDEJ sesji, w tej kolejności — skrót scripts/check.sh)
scripts/check.sh        # ruff check -> ruff format -> mypy src -> pytest + pokrycie
                        # na Windows bez bash/WSL: powershell -File scripts/check.ps1

# testy pojedynczo
pytest -q -x --tb=short                 # domyślnie pomija markery network i llm_live
pytest tests/test_config.py -k hash     # podzbiór
python scripts/split_spec.py            # regeneruje docs/sessions/Sxx.md z SPEC.md

# CLI (po instalacji)
gpwbot --help
gpwbot doctor --config config/config.yaml   # kod 0 = OK, 1 = błąd konfiguracji
```

Kody wyjścia CLI: `0` sukces, `1` błąd konfiguracji, `2` komenda nie zaimplementowana.

## Zasady pracy sesji (A1)

1. Na początku sesji czytaj TYLKO: ten plik, `docs/PROGRESS.md`, `docs/INTERFACES.md`,
   swój `docs/sessions/Sxx.md` i pliki z pola Wejścia. Nie czytaj całego repozytorium.
2. Nie wczytuj dużych wyników do kontekstu: `pytest -q -x --tb=short`, `head`, `tail`, `grep`.
3. Przy ~70% budżetu kontekstu zatrzymaj się w stabilnym punkcie (testy zielone), zapisz stan
   w `docs/PROGRESS.md` i zakończ. Nie zostawiaj czerwonych testów (wyjątek: `xfail` z uzasadnieniem).
4. Koniec sesji: `ruff check`, `ruff format`, `mypy src`, `pytest`, aktualizacja
   `docs/INTERFACES.md` i `docs/PROGRESS.md`, commit `Sxx: krótki opis`. Skrót: `scripts/check.sh`.
5. Nie zmieniaj publicznych sygnatur z wcześniejszych sesji bez wpisu w `docs/decisions.md`
   i aktualizacji wszystkich użyć oraz testów.

## Konwencje kodu (A3)

- Python 3.11+, układ `src/gpwbot`, pełne adnotacje typów, `mypy --strict` dla `src/gpwbot`.
- Ruff: reguły E, F, I, B, UP, SIM; długość linii 100 (konfiguracja w `pyproject.toml`).
- **Czas:** wszystkie `datetime` świadome strefy. W bazie UTC; Europe/Warsaw tylko na brzegach
  (kalendarz, raporty, CLI). Data sesji to `date` w czasie warszawskim. Zakaz `datetime.now()`
  poza `SystemClock`; reszta kodu używa `Clock` z `AppContext`.
- **Pieniądze/ceny:** `Decimal` w logice (ledger, fill model, risk), zaokrąglanie `ROUND_HALF_UP`,
  w bazie tekst przez `DecimalStr`. `pandas`/`float` tylko w warstwie cen i wskaźników; konwersja
  na granicy funkcją `to_decimal(x, places)`. Kwoty PLN do 0,01.
- **Zależności:** bez globalnego stanu; wszystko przez `AppContext` (frozen dataclass). Pola:
  S00 = `config`, `clock`; kolejne sesje dodają pola i aktualizują ten plik oraz INTERFACES.md.
- Czyste funkcje tam, gdzie się da (features, risk, fill model, metrics); I/O oddzielone od logiki.
- **Wyjątki** w `gpwbot/errors.py`: `GpwbotError` → `ConfigError`, `DataQualityError`,
  `ProviderError`, `LlmError`, `LlmValidationError`, `RiskViolation`, `LedgerError`,
  `RunLockError`, `ReplayMismatch`.
- **Logi:** `structlog`; pola kontekstowe `run_id` (przez `bind_run`), `component`, `ticker`.
  Zakaz `print` poza CLI.
- **Idempotencja:** zapisy z kluczem naturalnym przez upsert (`INSERT ... ON CONFLICT DO UPDATE`).
- **Prompty/schematy:** stała `PROMPT_VERSION` w każdym pliku promptu; `prompt_hash` = SHA-256
  wyrenderowanej treści.

## Testy (A4)

- Narzędzia: `pytest`, `pytest-cov`, `hypothesis`, `respx`; golden files w `tests/golden/`
  (aktualizacja flagą `--update-golden`).
- Markery: `unit` (domyślny), `integration`, `network` (prawdziwy HTTP), `llm_live` (prawdziwy LLM).
  Domyślnie pomijane: `-m "not network and not llm_live"` (ustawione w `pyproject.toml`).
- Globalne fixture w `tests/conftest.py`: S00 = `fake_clock`, `app_ctx`; kolejne sesje dodają
  `tmp_db` (S01), `make_prices` (S03), `fake_llm` (S11).
- Testy jednostkowe NIGDY nie łączą się z siecią. Dane testowe w `tests/fixtures/`.
- Pokrycie: moduły logiki (features, risk, ledger/fifo, fill_model, metrics, calendar, ticks) ≥ 90%,
  reszta ≥ 75%; sprawdza `scripts/check.sh` przez `scripts/check_coverage.py`.

## Pliki koordynacyjne (A2)

| Plik | Rola |
| --- | --- |
| `docs/SPEC.md` | pełna specyfikacja (Część I + II) |
| `docs/sessions/Sxx.md` | sekcje sesji wycięte skryptem `scripts/split_spec.py` |
| `docs/PROGRESS.md` | statusy S00–S15, decyzje, dług techniczny, pytania otwarte |
| `docs/INTERFACES.md` | publiczne sygnatury ukończonych modułów (bez kodu) — czytaj zamiast źródeł |
| `docs/decisions.md` | decyzje przy niejednoznacznościach i weryfikacje z sekcji 18 Części I |
