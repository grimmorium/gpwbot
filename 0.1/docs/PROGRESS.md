# PROGRESS — gpwbot 0.1

Statusy: `todo` / `in-progress` / `done`. Aktualizowany na końcu każdej sesji (A1.4).

## Tabela sesji

| Sesja | Zakres | Status | Uwagi |
| --- | --- | --- | --- |
| S00 | Szkielet, konfiguracja, narzędzia | done | pyproject, config/clock/context/logging/cli, check.sh(+ps1), split_spec → docs/sessions/; 53 testów zielone, pokrycie 98%, `gpwbot doctor` działa |
| S01 | Baza danych i migracje | todo | zależy od: S00 |
| S02 | Kalendarz GPW, tick size, koszty | todo | zależy od: S00 |
| S03 | Ceny: Stooq, jakość danych, ingest | todo | zależy od: S01, S02 |
| S04 | Universe, instrumenty, aliasy | todo | zależy od: S01, S03 |
| S05 | Wskaźniki i screener | todo | zależy od: S04 |
| S06 | Ledger, pozycje, partie FIFO | todo | zależy od: S01, S02 |
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

Pełna lista w `docs/decisions.md`. Z S00: D-001…D-008 (placeholdery sekcji config, nadpisywania
ze środowiska, ścieżki, kody wyjścia CLI, logowanie structlog, check.ps1 na Windows, pokrycie).

## Dług techniczny

- Sekcje `screener`, `baseline`, `text`, `eval` w konfiguracji to placeholdery — rozbudowa
  w S05/S09/S10–S12/S14 (D-001).
- `AppContext` ma tylko pola `config` i `clock`; kolejne sesje dodają pola (A3).

## Pytania otwarte

- Brak na koniec S00. Weryfikacje z sekcji 18 Części I przejęte do `docs/decisions.md`
  wraz z sesjami odpowiedzialnymi (S02, S03, S11).
