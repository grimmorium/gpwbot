#!/usr/bin/env python3
"""Agent giełdowy GPW — typuje transakcje dla portfela przechowywanego przez app.py.

Przepływ:
  1. odczyt portfela:   python app.py -l
  2. notowania GPW:     Stooq (CSV), w razie niepowodzenia Yahoo Finance
  3. newsy:             Google News RSS (nagłówki z ostatnich dni)
  4. lokalny LLM:       API zgodne z OpenAI (/v1/chat/completions), np. llama.cpp server
  5. walidacja zleceń:  w kodzie (stan gotówki, stan posiadania, limity) — nie ufamy LLM-owi
  6. zapis:             python app.py -n "PLN:-KWOTA|TICKER:ILOSC"   (tylko z --execute)

Konwencja portfela (zgodna z app.py, które zapisuje zawsze DWA wpisy naraz):
  - gotówka to pozycja o nazwie PLN (zmień przez --cash-name),
  - akcje to pozycje o nazwie = ticker (np. PKN), wartość = liczba sztuk,
  - kupno 10 szt. PKN po 65.40:  PLN:-654.00|PKN:10
  - sprzedaż 10 szt. PKN:        PLN:654.00|PKN:-10
  Początkowy budżet wprowadź raz, np.:  python app.py -n "PLN:10000|PLN_START:0"

Tylko biblioteki standardowe. Domyślnie TRYB SUCHY — nic nie jest zapisywane bez --execute.
To narzędzie edukacyjne/eksperymentalne, nie jest poradą inwestycyjną.
"""

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (X11; Linux x86_64) gpw-agent/1.0"

# Domyślne uniwersum (okolice WIG20) — nadpisz przez --universe-file / --tickers.
# Format pliku JSON: {"PKN": "Orlen", "PKO": "PKO Bank Polski", ...}
DEFAULT_UNIVERSE = {
    "PKN": "Orlen", "PKO": "PKO Bank Polski", "PZU": "PZU", "PEO": "Bank Pekao",
    "KGH": "KGHM", "DNP": "Dino Polska", "ALE": "Allegro", "LPP": "LPP",
    "CDR": "CD Projekt", "PGE": "PGE", "SPL": "Santander Bank Polska",
    "MBK": "mBank", "KTY": "Grupa Kęty", "OPL": "Orange Polska",
    "PCO": "Pepco Group", "ALR": "Alior Bank", "KRU": "Kruk",
    "BDX": "Budimex", "TPE": "Tauron", "JSW": "JSW",
}
MACRO_QUERIES = [
    "GPW WIG20 sesja", "RPP stopy procentowe", "inflacja Polska", "rynki akcji Europa",
]


# --------------------------------------------------------------------------- HTTP

def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- portfel (app.py)

def run_app(app: Path, db: Path | None, *extra: str) -> str:
    cmd = [sys.executable, str(app), *extra]
    if db:
        cmd += ["-d", str(db)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    if res.returncode != 0:
        raise RuntimeError(f"app.py zwróciło błąd ({res.returncode}): {res.stderr.strip()}")
    return res.stdout


def read_portfolio(app: Path, db: Path | None) -> dict[str, Decimal]:
    """Parsuje wyjście `app.py -l` (nagłówek + wiersze 'NAZWA   KWOTA')."""
    out = run_app(app, db, "-l")
    items: dict[str, Decimal] = {}
    for line in out.splitlines()[1:]:  # pierwszy wiersz to nagłówek
        if not line.strip():
            continue
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            continue
        try:
            items[parts[0].strip()] = Decimal(parts[1])
        except InvalidOperation:
            log(f"[portfel] pominięto nieparsowalny wiersz: {line!r}")
    return items


# --------------------------------------------------------------------------- notowania

@dataclass
class Bar:
    date: str
    high: float
    low: float
    close: float
    volume: float


def fetch_stooq(symbol: str) -> tuple[list[Bar], str]:
    url = f"https://stooq.pl/q/d/l/?s={urllib.parse.quote(symbol.lower())}&i=d"
    text = http_get(url)
    lines = text.strip().splitlines()
    if not lines or not lines[0].lower().startswith("date"):
        raise RuntimeError(f"Stooq zwrócił nie-CSV: {text[:80]!r}")
    head = [h.strip().lower() for h in lines[0].split(",")]
    bars = []
    for ln in lines[1:]:
        row = dict(zip(head, ln.split(",")))
        try:
            bars.append(Bar(row["date"], float(row["high"]), float(row["low"]),
                            float(row["close"]), float(row.get("volume") or 0)))
        except (KeyError, ValueError):
            continue
    if not bars:
        raise RuntimeError("Stooq: brak danych")
    return bars[-150:], url


def fetch_yahoo(symbol: str) -> tuple[list[Bar], str]:
    ysym = "WIG20.WA" if symbol.upper() == "WIG20" else f"{symbol.upper()}.WA"
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(ysym)}?range=6mo&interval=1d")
    data = json.loads(http_get(url))
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        bars.append(Bar(datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                        q["high"][i] or c, q["low"][i] or c, c, q["volume"][i] or 0))
    if not bars:
        raise RuntimeError("Yahoo: brak danych")
    return bars, url


def get_history(symbol: str) -> tuple[list[Bar], dict]:
    """Zwraca (słupki, {"provider": nazwa_źródła, "url": adres_pobrania})."""
    errors = []
    for name, fn in (("stooq", fetch_stooq), ("yahoo", fetch_yahoo)):
        try:
            bars, url = fn(symbol)
            return bars, {"provider": name, "url": url}
        except Exception as exc:  # noqa: BLE001 — chcemy spróbować kolejnego źródła
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors))


def sma(vals: list[float], n: int):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def pct_change(vals: list[float], n: int):
    return (vals[-1] / vals[-1 - n] - 1) * 100 if len(vals) > n and vals[-1 - n] else None


def rsi(vals: list[float], n: int = 14):
    if len(vals) <= n:
        return None
    diffs = [b - a for a, b in zip(vals[-n - 1:-1], vals[-n:])]
    gain = sum(d for d in diffs if d > 0) / n
    loss = -sum(d for d in diffs if d < 0) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def volatility(vals: list[float], n: int = 20):
    if len(vals) <= n:
        return None
    rets = [vals[i] / vals[i - 1] - 1 for i in range(len(vals) - n, len(vals))]
    return statistics.pstdev(rets) * 100


def indicators(bars: list[Bar]) -> dict:
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    avg_vol = sma(vols, 20)
    return {
        "date": bars[-1].date,
        "close": closes[-1],
        "d1": pct_change(closes, 1), "d5": pct_change(closes, 5), "d20": pct_change(closes, 20),
        "sma20": sma(closes, 20), "sma50": sma(closes, 50),
        "rsi14": rsi(closes), "vol20": volatility(closes),
        "volx": (vols[-1] / avg_vol) if avg_vol else None,
        "hi60": max(b.high for b in bars[-60:]), "lo60": min(b.low for b in bars[-60:]),
    }


def f(x, nd=2, plus=False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/d"
    return f"{x:+.{nd}f}" if plus else f"{x:.{nd}f}"


def market_line(ticker: str, name: str, ind: dict) -> str:
    return (f"{ticker} ({name}) | kurs {f(ind['close'])} z {ind['date']} | "
            f"1d {f(ind['d1'], 1, True)}% | 5d {f(ind['d5'], 1, True)}% | 20d {f(ind['d20'], 1, True)}% | "
            f"SMA20 {f(ind['sma20'])} | SMA50 {f(ind['sma50'])} | RSI14 {f(ind['rsi14'], 0)} | "
            f"zmienność20d {f(ind['vol20'], 1)}% | wolumen/śr20d {f(ind['volx'], 1)}x | "
            f"min60d {f(ind['lo60'])} max60d {f(ind['hi60'])}")


# --------------------------------------------------------------------------- newsy

def google_news(query: str, limit: int, days: int) -> tuple[list[str], str]:
    q = urllib.parse.quote(f"{query} when:{days}d")
    url = f"https://news.google.com/rss/search?q={q}&hl=pl&gl=PL&ceid=PL:pl"
    root = ET.fromstring(http_get(url))
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        date = (item.findtext("pubDate") or "")[5:16]
        if title:
            out.append(f"{date} {title}".strip())
        if len(out) >= limit:
            break
    return out, url


# --------------------------------------------------------------------------- prompt

SYSTEM_PROMPT = """Jesteś ostrożnym analitykiem giełdowym specjalizującym się w Giełdzie Papierów \
Wartościowych w Warszawie (GPW). Zarządzasz WIRTUALNYM portfelem i proponujesz transakcje.

Zasady:
- Operujesz wyłącznie na tickerach z sekcji NOTOWANIA. Nie wymyślaj danych, których nie dostałeś.
- Tylko pozycje długie: bez krótkiej sprzedaży, bez dźwigni. Zakup nie może przekroczyć dostępnej gotówki, \
sprzedaż nie może przekroczyć liczby posiadanych sztuk.
- Ilości są całkowite (pełne akcje). Zakładaj wykonanie po podanym kursie.
- Pilnuj dywersyfikacji: żadna pozycja nie powinna przekraczać {max_pos}% wartości portfela.
- Nie handluj dla samego handlu. Pusta lista zleceń to w pełni poprawna decyzja.
- Uzasadnienie każdego zlecenia: 1–2 zdania, odwołanie do konkretnych danych (trend, RSI, newsy).
- Uwzględnij koszt prowizji ({fee}% na transakcję).

Odpowiedz WYŁĄCZNIE jednym obiektem JSON, bez markdowna i bez tekstu poza nim:
{{"analysis": "krótkie podsumowanie sytuacji (max 5 zdań)",
  "orders": [{{"ticker": "PKN", "side": "BUY" lub "SELL", "quantity": liczba_całkowita, "reason": "..."}}]}}
Maksymalnie {max_orders} zleceń."""


def build_user_prompt(now, cash_name, cash, portfolio_rows, total, market_lines, index_line,
                      news_by_ticker, macro_news, warnings) -> str:
    parts = [f"Data i czas: {now:%Y-%m-%d %H:%M} (czas lokalny). Waluta: PLN.", ""]
    parts.append(f"## PORTFEL (wartość całkowita ≈ {total:.2f} PLN)")
    parts.append(f"Gotówka ({cash_name}): {cash:.2f}")
    parts += portfolio_rows or ["Brak pozycji akcyjnych."]
    parts += ["", "## NOTOWANIA (dzienne, ostatnie zamknięcie)"]
    if index_line:
        parts.append(index_line)
    parts += market_lines
    parts += ["", "## NEWSY SPÓŁEK (nagłówki z ostatnich dni)"]
    for t, items in news_by_ticker.items():
        if items:
            parts.append(f"[{t}]")
            parts += [f"- {x}" for x in items]
    parts += ["", "## NEWSY OGÓLNE / MAKRO"]
    parts += [f"- {x}" for x in macro_news] or ["brak danych"]
    if warnings:
        parts += ["", "## OSTRZEŻENIA O DANYCH"] + [f"- {w}" for w in warnings]
    parts += ["", "Zaproponuj transakcje na dziś (JSON zgodny z opisem)."]
    return "\n".join(parts)


# --------------------------------------------------------------------------- LLM

def chat(base_url, model, api_key, messages, temperature, max_tokens, timeout) -> str:
    body = {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"] or ""


def extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)  # modele "myślące"
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("brak obiektu JSON w odpowiedzi")
    return json.loads(text[start:end + 1])


def ask_llm(args, system: str, user: str) -> tuple[dict, str]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    raw = ""
    for attempt in (1, 2):
        raw = chat(args.base_url, args.model, args.api_key, messages,
                   args.temperature, args.max_tokens, args.timeout)
        try:
            return extract_json(raw), raw
        except ValueError as exc:  # JSONDecodeError dziedziczy po ValueError
            log(f"[llm] próba {attempt}: niepoprawny JSON ({exc})")
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "To nie jest poprawny JSON. "
                          "Zwróć wyłącznie obiekt JSON zgodny ze schematem."}]
    raise RuntimeError(f"LLM nie zwrócił poprawnego JSON-a. Ostatnia odpowiedź:\n{raw[:1000]}")


# --------------------------------------------------------------------------- walidacja zleceń

@dataclass
class Tx:
    ticker: str
    side: str
    qty: int
    price: Decimal
    cash_delta: Decimal
    reason: str

    def cli_arg(self, cash_name: str) -> str:
        return f"{cash_name}:{format(self.cash_delta, 'f')}|{self.ticker}:{self.qty if self.side == 'BUY' else -self.qty}"


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_orders(raw_orders, cash: Decimal, holdings: dict[str, Decimal],
                    prices: dict[str, Decimal], fee: Decimal, max_orders: int,
                    max_pos_pct: Decimal) -> tuple[list[Tx], list[str]]:
    notes: list[str] = []
    accepted: list[Tx] = []
    if not isinstance(raw_orders, list):
        return [], ["Pole 'orders' nie jest listą — odrzucono."]
    holdings = dict(holdings)
    total = cash + sum(q * prices[t] for t, q in holdings.items() if t in prices)

    for o in raw_orders:
        if len(accepted) >= max_orders:
            notes.append("Przekroczono limit liczby zleceń — pozostałe odrzucono.")
            break
        try:
            ticker = str(o["ticker"]).strip().upper()
            side = str(o["side"]).strip().upper()
            qd = Decimal(str(o["quantity"]))
        except (KeyError, TypeError, InvalidOperation):
            notes.append(f"Odrzucono (zły format): {o!r}")
            continue
        reason = str(o.get("reason", ""))[:300]
        if side not in ("BUY", "SELL") or qd <= 0 or qd != qd.to_integral_value():
            notes.append(f"Odrzucono (strona/ilość): {o!r}")
            continue
        price = prices.get(ticker)
        if price is None:
            notes.append(f"Odrzucono {ticker}: brak wyceny / ticker spoza uniwersum.")
            continue
        qty = int(qd)

        if side == "BUY":
            unit = price * (1 + fee)
            by_cash = int(cash // unit)
            by_limit = int(max(Decimal(0), total * max_pos_pct / 100 - holdings.get(ticker, 0) * price) // price)
            allowed = min(qty, by_cash, by_limit)
            if allowed < qty:
                notes.append(f"BUY {ticker}: zmniejszono {qty} -> {allowed} "
                             f"(gotówka: {by_cash}, limit pozycji: {by_limit}).")
            if allowed <= 0:
                continue
            cost = q2(allowed * unit)
            cash -= cost
            holdings[ticker] = holdings.get(ticker, Decimal(0)) + allowed
            accepted.append(Tx(ticker, side, allowed, price, -cost, reason))
        else:
            held = int(holdings.get(ticker, Decimal(0)))
            allowed = min(qty, held)
            if allowed < qty:
                notes.append(f"SELL {ticker}: zmniejszono {qty} -> {allowed} (posiadane: {held}).")
            if allowed <= 0:
                continue
            proceeds = q2(allowed * price * (1 - fee))
            cash += proceeds
            holdings[ticker] -= allowed
            accepted.append(Tx(ticker, side, allowed, price, proceeds, reason))
    return accepted, notes


# --------------------------------------------------------------------------- main

def load_universe(args) -> dict[str, str]:
    if args.universe_file:
        uni = json.loads(Path(args.universe_file).read_text(encoding="utf-8"))
    else:
        uni = dict(DEFAULT_UNIVERSE)
    if args.tickers:
        wanted = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        uni = {t: uni.get(t, t) for t in wanted}
    return {k.upper(): v for k, v in uni.items()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Agent giełdowy GPW (lokalny LLM + app.py).")
    p.add_argument("--app", type=Path, default=HERE / "app.py", help="ścieżka do app.py")
    p.add_argument("-d", "--db", type=Path, default=None, help="plik bazy przekazywany do app.py")
    p.add_argument("--cash-name", default="PLN", help="nazwa pozycji gotówkowej (domyślnie PLN)")
    p.add_argument("--universe-file", help="JSON {ticker: nazwa} z listą spółek")
    p.add_argument("--tickers", help="lista tickerów po przecinku (zawęża uniwersum)")
    p.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://localhost:8080/v1"))
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "local"))
    p.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "sk-local"))
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--timeout", type=int, default=900, help="timeout zapytania do LLM [s]")
    p.add_argument("--fee", type=Decimal, default=Decimal("0"), help="prowizja w %% na transakcję")
    p.add_argument("--max-orders", type=int, default=5)
    p.add_argument("--max-position-pct", type=Decimal, default=Decimal("25"))
    p.add_argument("--news-per-ticker", type=int, default=3)
    p.add_argument("--news-days", type=int, default=3)
    p.add_argument("--execute", action="store_true", help="zapisz transakcje przez app.py -n")
    p.add_argument("--yes", action="store_true", help="z --execute: bez pytania o potwierdzenie")
    p.add_argument("--show-prompt", action="store_true", help="wypisz prompt wysyłany do LLM")
    p.add_argument("--runs-dir", type=Path, default=HERE / "agent_runs")
    args = p.parse_args(argv)

    fee_frac = args.fee / 100
    now = datetime.now()
    warnings: list[str] = []

    # 1. portfel
    log("[1/5] Odczyt portfela…")
    items = read_portfolio(args.app, args.db)
    cash = items.pop(args.cash_name, Decimal(0))
    holdings = {k: v for k, v in items.items() if v > 0}
    for k, v in items.items():
        if v < 0:
            warnings.append(f"Pozycja {k} ma ujemny stan ({v}) — zignorowana.")

    # 2. notowania
    universe = load_universe(args)
    for t in holdings:
        universe.setdefault(t.upper(), t)  # posiadane spoza uniwersum też wyceniamy
    log(f"[2/5] Pobieranie notowań ({len(universe)} spółek)…")
    prices: dict[str, Decimal] = {}
    market_lines: list[str] = []
    quote_sources: dict[str, dict] = {}
    for t, name in universe.items():
        try:
            bars, src = get_history(t)
            quote_sources[t] = src
            ind = indicators(bars)
            prices[t] = Decimal(str(ind["close"]))
            market_lines.append(market_line(t, name, ind))
            age = (now.date() - datetime.strptime(ind["date"], "%Y-%m-%d").date()).days
            if age > 5:
                warnings.append(f"{t}: ostatnie notowanie sprzed {age} dni (źródło {src['provider']}).")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{t}: brak notowań ({exc}).")
            log(f"  ! {t}: {exc}")
    index_line = None
    try:
        ibars, isrc = get_history("WIG20")
        quote_sources["WIG20"] = isrc
        index_line = market_line("WIG20", "indeks", indicators(ibars))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Brak danych indeksu WIG20 ({exc}).")
    if not prices:
        log("Brak jakichkolwiek notowań — przerywam.")
        return 2

    holdings_valued = {t: q for t, q in holdings.items() if t in prices}
    for t in holdings:
        if t not in prices:
            warnings.append(f"Pozycja {t} bez wyceny — nie można nią handlować.")
    total = cash + sum(q * prices[t] for t, q in holdings_valued.items())
    portfolio_rows = [
        f"{t}: {q} szt. × {prices[t]:.2f} = {q * prices[t]:.2f} PLN "
        f"({(q * prices[t] / total * 100) if total else 0:.1f}% portfela)"
        for t, q in holdings_valued.items()
    ]

    # 3. newsy
    log("[3/5] Pobieranie newsów…")
    news: dict[str, list[str]] = {}
    news_sources: dict[str, str] = {}
    for t, name in universe.items():
        if t not in prices:
            continue
        try:
            items, url = google_news(f"{name} GPW", args.news_per_ticker, args.news_days)
            news[t] = items
            news_sources[t] = url
        except Exception as exc:  # noqa: BLE001
            news[t] = []
            log(f"  ! newsy {t}: {exc}")
    macro: list[str] = []
    macro_sources: list[str] = []
    for q in MACRO_QUERIES:
        try:
            items, url = google_news(q, 3, args.news_days)
            macro += items
            macro_sources.append(url)
        except Exception as exc:  # noqa: BLE001
            log(f"  ! newsy '{q}': {exc}")

    # 4. LLM
    system = SYSTEM_PROMPT.format(max_pos=args.max_position_pct, fee=args.fee,
                                  max_orders=args.max_orders)
    user = build_user_prompt(now, args.cash_name, cash, portfolio_rows, total,
                             market_lines, index_line, news, macro, warnings)
    if args.show_prompt:
        print("=== SYSTEM ===\n" + system + "\n\n=== USER ===\n" + user + "\n", file=sys.stderr)
    log(f"[4/5] Zapytanie do LLM ({args.base_url}, model {args.model})…")
    result, raw = ask_llm(args, system, user)

    # 5. walidacja
    txs, notes = validate_orders(result.get("orders", []), cash, holdings_valued, prices,
                                 fee_frac, args.max_orders, args.max_position_pct)

    print("\n=== ANALIZA LLM ===")
    print(result.get("analysis", "(brak)"))
    print("\n=== TRANSAKCJE (po walidacji) ===")
    if not txs:
        print("Brak transakcji.")
    for tx in txs:
        print(f"{tx.side:<4} {tx.ticker:<5} {tx.qty:>6} szt. @ {tx.price:.2f}  "
              f"({'+' if tx.cash_delta > 0 else ''}{tx.cash_delta} {args.cash_name})  — {tx.reason}")
        print(f"     app.py -n \"{tx.cli_arg(args.cash_name)}\"")
    if notes:
        print("\n=== UWAGI WALIDATORA ===")
        for n in notes:
            print(f"- {n}")

    # log przebiegu
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    run_file = args.runs_dir / f"run_{now:%Y%m%d_%H%M%S}.json"
    run_file.write_text(json.dumps({
        "time": now.isoformat(), "system": system, "user": user, "llm_raw": raw,
        "orders": [{"ticker": t.ticker, "side": t.side, "qty": t.qty, "price": str(t.price),
                    "cash_delta": str(t.cash_delta), "reason": t.reason} for t in txs],
        "notes": notes,
        "sources": {
            "quotes": quote_sources,
            "news_tickers": news_sources,
            "news_macro": macro_sources,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\nZapisano log: {run_file}")

    # 6. zapis
    if not txs:
        return 0
    if not args.execute:
        print("\nTRYB SUCHY — nic nie zapisano. Użyj --execute, aby wprowadzić transakcje.")
        return 0
    if not args.yes:
        if input("\nZapisać powyższe transakcje w bazie? [t/N] ").strip().lower() not in ("t", "y", "tak"):
            print("Anulowano.")
            return 0
    for tx in txs:
        run_app(args.app, args.db, "-n", tx.cli_arg(args.cash_name))
    print(f"Zapisano {len(txs)} transakcji. Aktualny stan portfela:")
    print(run_app(args.app, args.db, "-l"))
    return 0


if __name__ == "__main__":
    sys.exit(main())