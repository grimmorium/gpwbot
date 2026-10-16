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

## Weryfikacje z sekcji 18 Części I (do domknięcia w sesjach)

| # | Kwestia | Odpowiedzialna sesja | Status |
| --- | --- | --- | --- |
| V-01 | Aktualne godziny sesji GPW, widełki wahań, T+2 vs T+1 (regulaminy GPW/KDPW) | S02 | otwarte — domyślne z sekcji 2 Części I |
| V-02 | Tabela tick size (źródło: regulamin GPW) | S02 | otwarte — wartości przykładowe |
| V-03 | Dostępność, format i warunki użycia Stooq / yfinance / RSS / ESPI; czy ceny są skorygowane | S03 | otwarte |
| V-04 | Która flaga llama.cpp steruje trybem rozumowania Qwen (`enable_thinking`?) | S11 | otwarte — config ma `thinking_extraction`/`thinking_decision` |
