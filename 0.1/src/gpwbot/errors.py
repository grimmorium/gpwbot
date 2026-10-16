"""Hierarchia wyjątków domenowych gpwbot (A3)."""


class GpwbotError(Exception):
    """Baza wszystkich błędów domenowych gpwbot."""


class ConfigError(GpwbotError):
    """Błędna konfiguracja (plik, walidacja, zmienne środowiskowe)."""


class DataQualityError(GpwbotError):
    """Dane źródłowe nie przeszły kontroli jakości."""


class ProviderError(GpwbotError):
    """Awaria adaptera danych (HTTP, parser, limit zapytań)."""


class LlmError(GpwbotError):
    """Błąd komunikacji z serwerem LLM lub błędna odpowiedź."""


class LlmValidationError(LlmError):
    """Odpowiedź LLM nie spełnia schematu (po wyczerpaniu ponowień)."""


class RiskViolation(GpwbotError):
    """Decyzja narusza reguły Risk Managera."""


class LedgerError(GpwbotError):
    """Naruszenie księgi portfela (np. sprzedaż większa niż stan)."""


class RunLockError(GpwbotError):
    """Run o tej samej parze (name, as_of) jest już zarejestrowany/blokowany."""


class ReplayMismatch(GpwbotError):
    """Replay nie odtworzył identycznych zleceń jak oryginalny run."""
