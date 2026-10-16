"""Interfejs wiersza poleceń gpwbot (Typer).

Kody wyjścia: 0 = sukces, 1 = błąd konfiguracji, 2 = komenda nie zaimplementowana.
Jedyne miejsce dozwolone na `print`/echo (A3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from gpwbot import __version__
from gpwbot.config import load_config
from gpwbot.db.migrate import upgrade_to_head
from gpwbot.errors import ConfigError

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


@app.command()
def ingest(
    since: Annotated[str, typer.Option("--since", help="Okno czasowe pobierania danych.")] = "1d",
) -> None:
    """Pobiera dane (ceny/teksty)."""
    _not_implemented("ingest")


@app.command(name="backfill-prices")
def backfill_prices(
    years: Annotated[int, typer.Option("--years", help="Liczba lat historii.")] = 5,
) -> None:
    """Pobiera historię cen."""
    _not_implemented("backfill-prices")


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
