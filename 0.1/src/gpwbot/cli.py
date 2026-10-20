"""Interfejs wiersza poleceń gpwbot (Typer).

Kody wyjścia: 0 = sukces, 1 = błąd konfiguracji, 2 = komenda nie zaimplementowana.
Jedyne miejsce dozwolone na `print`/echo (A3).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer

from gpwbot import __version__
from gpwbot.clock import SystemClock
from gpwbot.config import load_config
from gpwbot.context import build_context
from gpwbot.db.migrate import upgrade_to_head
from gpwbot.errors import ConfigError, GpwbotError
from gpwbot.ingest.prices_job import (
    IngestReport,
    backfill_prices,
    default_price_tickers,
    ingest_prices,
)

WARSAW = ZoneInfo("Europe/Warsaw")

app = typer.Typer(
    name="gpwbot",
    help="Agent AI typujący akcje GPW (paper trading, lokalny LLM).",
    no_args_is_help=True,
)

db_app = typer.Typer(help="Operacje na bazie danych.")
app.add_typer(db_app, name="db")


def _config_path(ctx: typer.Context) -> Path:
    obj = ctx.obj
    return Path(obj) if obj is not None else Path("config/config.yaml")


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path, typer.Option("--config", help="Ścieżka do pliku konfiguracyjnego.")
    ] = Path("config/config.yaml"),
) -> None:
    """gpwbot — agent AI typujący akcje GPW (paper trading)."""
    ctx.obj = config


@app.command()
def version() -> None:
    """Wyświetla wersję gpwbot."""
    typer.echo(f"gpwbot {__version__}")


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Ładuje konfigurację i wypisuje hash oraz ścieżki; kod 1 przy błędzie."""
    try:
        cfg = load_config(_config_path(ctx))
    except ConfigError as exc:
        typer.echo(f"błąd konfiguracji: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"hash: {cfg.config_hash()}")
    for name in ("db_path", "data_dir", "raw_dir", "reports_dir"):
        typer.echo(f"{name}: {getattr(cfg.paths, name)}")


@db_app.command()
def upgrade(ctx: typer.Context) -> None:
    """Stosuje migracje Alembic do head (tworzy bazę od zera)."""
    try:
        cfg = load_config(_config_path(ctx))
    except ConfigError as exc:
        typer.echo(f"błąd konfiguracji: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    upgrade_to_head(cfg.paths.db_path)
    typer.echo(f"migracje zastosowane do head: {cfg.paths.db_path}")


def _not_implemented(name: str) -> None:
    typer.echo(f"komenda '{name}' nie jest jeszcze zaimplementowana (S00)", err=True)
    raise typer.Exit(code=2)


def _parse_since(value: str, end: dt.date) -> dt.date:
    """`Nd` (dni kalendarzowe) → data początkowa; inny kształt to `ConfigError`."""
    match = re.fullmatch(r"(\d+)d", value.strip())
    if match is None or int(match.group(1)) < 1:
        raise ConfigError(f"--since musi mieć postać Nd (dni), otrzymano {value!r}")
    return end - dt.timedelta(days=int(match.group(1)))


def _print_report(title: str, report: IngestReport) -> None:
    typer.echo(f"{title}: {report.start.isoformat()}..{report.end.isoformat()}")
    for result in report.results:
        if result.ok:
            extra = f" problemy_jakosci={len(result.issues)}" if result.issues else ""
            typer.echo(f"OK: {result.ticker} (bars={result.bars_upserted}{extra})")
        else:
            typer.echo(f"BŁĄD: {result.ticker} — {result.error}")
    failed = len(report.failed_tickers)
    typer.echo(
        f"razem: upserted={report.total_bars_upserted} issues={report.total_issues} failed={failed}"
    )


@app.command()
def ingest(
    ctx: typer.Context,
    prices: Annotated[bool, typer.Option("--prices", help="Pobiera ceny dzienne.")] = False,
    since: Annotated[
        str, typer.Option("--since", help="Okno czasowe pobierania danych (Nd).")
    ] = "1d",
) -> None:
    """Pobiera dane (ceny/teksty); `--prices` włącza ingest cen ze Stooq."""
    if not prices:
        _not_implemented("ingest")
    try:
        context = build_context(_config_path(ctx))
        end = SystemClock().now().astimezone(WARSAW).date()  # brzeg CLI (data sesji)
        start = _parse_since(since, end)
    except ConfigError as exc:
        typer.echo(f"błąd konfiguracji: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    report = ingest_prices(context, default_price_tickers(context.config), start, end)
    _print_report("ingest prices", report)


@app.command(name="backfill-prices")
def backfill(
    ctx: typer.Context,
    years: Annotated[int, typer.Option("--years", help="Liczba lat historii.")] = 5,
) -> None:
    """Pobiera historię cen partiami rocznymi."""
    try:
        context = build_context(_config_path(ctx))
    except ConfigError as exc:
        typer.echo(f"błąd konfiguracji: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        report = backfill_prices(context, years)
    except (ValueError, GpwbotError) as exc:
        typer.echo(f"błąd: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _print_report("backfill prices", report)


@app.command()
def run(
    name: Annotated[str, typer.Argument(help="Nazwa runu (np. morning).")],
    as_of: Annotated[str | None, typer.Option("--as-of", help="Chwiel T w formacie ISO.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Uruchamia run decyzyjny."""
    _not_implemented("run")


@app.command()
def replay(
    run_id: Annotated[int, typer.Option("--run-id", help="Identyfikator runu do odtworzenia.")] = 0,
) -> None:
    """Odtwarza run z zapisanych odpowiedzi LLM."""
    _not_implemented("replay")


@app.command()
def report(
    date: Annotated[str, typer.Option("--date", help="Data raportu (YYYY-MM-DD).")] = "",
) -> None:
    """Generuje raport dzienny."""
    _not_implemented("report")


@app.command(name="eval")
def eval_cmd(
    from_date: Annotated[
        str, typer.Option("--from", help="Data początkowa oceny (YYYY-MM-DD).")
    ] = "",
) -> None:
    """Ocena jakości decyzji."""
    _not_implemented("eval")
