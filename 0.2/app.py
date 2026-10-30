"""Aplikacja OPERATIONS — wprowadzanie wpisów do tabeli [OPERATIONS].

Tylko biblioteki standardowe: sqlite3 (baza), tkinter (GUI), argparse (CLI).

Uruchomienie GUI:  python app.py
Tryb konsolowy:    python app.py -l   (wypisuje dane z zakładki TOTAL na stdout)
Dodawanie wpisów:  python app.py -n NAZWA1:KWOTA1|NAZWA2:KWOTA2
                   (np. python app.py -n ITM1:-100.2|ITM2:23; działa też w połączeniu z -l)
Filtrowanie TOTAL: python app.py -l -t "YYYYMMDD HH:mm:SS.ms"
                   (pokaż tylko wpisy, dla których TRANS_DATE_TIME <= podana wartość)
Opcjonalnie:       -d SCIEZKA         (plik bazy SQLite; domyślnie operations.db obok skryptu)

Uwaga: DDL ze specyfikacji ("ID IDENTITY AUTO_INCREMENT") nie jest poprawnym SQL żadnego
silnika — w SQLite odpowiednikiem kolumny tożsamościowej jest INTEGER PRIMARY KEY AUTOINCREMENT.
Pozostałe typy (VARCHAR(50), NUMBER(20,5)) oraz zapytania SELECT/INSERT działają bez zmian.
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "operations.db"
MAX_NAME_LEN = 50    # VARCHAR(50)
MAX_PRECISION = 20   # NUMBER(20,5) — cyfr znaczących łącznie
MAX_SCALE = 5        # ... miejsc po przecinku

SCHEMA = """
CREATE TABLE IF NOT EXISTS [OPERATIONS] (
    ID INTEGER PRIMARY KEY AUTOINCREMENT,
    ITEM_NAME VARCHAR(50) NOT NULL,
    ITEM_AMOUNT NUMBER(20,5),
    TRANS_DATE_TIME TIMESTAMP NOT NULL
);
"""

QUERY_TOTAL = """
SELECT ITEM_NAME, SUM(ITEM_AMOUNT)
FROM [OPERATIONS]
GROUP BY ITEM_NAME HAVING SUM(ITEM_AMOUNT) != 0;
"""

QUERY_TOTAL_UP_TO = """
SELECT ITEM_NAME, SUM(ITEM_AMOUNT)
FROM [OPERATIONS]
WHERE TRANS_DATE_TIME <= ?
GROUP BY ITEM_NAME HAVING SUM(ITEM_AMOUNT) != 0;
"""

QUERY_LIST = """
SELECT ID, ITEM_NAME, ITEM_AMOUNT, TRANS_DATE_TIME
FROM [OPERATIONS]
ORDER BY TRANS_DATE_TIME, ID;
"""

INSERT_SQL = (
    "INSERT INTO [OPERATIONS](ITEM_NAME, ITEM_AMOUNT, TRANS_DATE_TIME) VALUES (?, ?, ?);"
)


def _migrate(conn) -> None:
    """Migracja baz ze starą strukturą — dodaje kolumnę TRANS_DATE_TIME.

    Starsze wiersze dostają znacznik '00010101 00:00:00.000': ich czas transakcji jest nieznany.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(OPERATIONS)")}
    if "TRANS_DATE_TIME" not in cols:
        conn.execute(
            "ALTER TABLE [OPERATIONS] ADD COLUMN TRANS_DATE_TIME TIMESTAMP NOT NULL "
            "DEFAULT '00010101 00:00:00.000';"
        )


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def fetch_total(conn, trans_dt=None):
    if trans_dt is None:
        return conn.execute(QUERY_TOTAL).fetchall()
    return conn.execute(QUERY_TOTAL_UP_TO, (trans_dt,)).fetchall()


def fetch_list(conn):
    return conn.execute(QUERY_LIST).fetchall()


def trans_timestamp() -> str:
    """Bieżąca data i czas w formacie [YYYYMMDD HH:mm:SS.ms], np. 20261030 14:35:27.123."""
    now = datetime.now()
    return f"{now:%Y%m%d %H:%M:%S}.{now.microsecond // 1000:03d}"


def insert_operations(conn, name1: str, amount1: Decimal, name2: str, amount2: Decimal) -> None:
    stamp = trans_timestamp()  # obie pozycje transakcji dostają identyczny znacznik czasu
    with conn:  # jedna transakcja — oba wpisy lub żaden
        conn.execute(INSERT_SQL, (name1, float(amount1), stamp))
        conn.execute(INSERT_SQL, (name2, float(amount2), stamp))


_AMOUNT_RE = re.compile(r"^[+-]?\d+(?:\.\d*)?$")


def validate_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise ValueError("Pole Item name jest wymagane.")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(
            f"Item name może mieć co najwyżej {MAX_NAME_LEN} znaków (podano {len(name)})."
        )
    return name


def parse_amount(raw: str) -> Decimal:
    s = raw.strip()
    if not s:
        raise ValueError("Pole Item amount jest wymagane.")
    if "," in s and "." in s:
        raise ValueError("Item amount: użyj jednego separatora dziesiętnego (, lub .).")
    t = s.replace(",", ".")
    if not _AMOUNT_RE.match(t):
        raise ValueError(f"Item amount: nieprawidłowa liczba ({s!r}).")
    int_part, _, frac_part = t.partition(".")
    scale = len(frac_part)
    precision = max(len(int_part.lstrip("0")), 1) + scale
    if scale > MAX_SCALE:
        raise ValueError(f"Item amount: maksymalnie {MAX_SCALE} miejsc po przecinku.")
    if precision > MAX_PRECISION:
        raise ValueError(
            f"Item amount: maksymalnie {MAX_PRECISION} cyfr znaczących (NUMBER(20,5))."
        )
    return Decimal(t)


def format_amount(value) -> str:
    return f"{Decimal(str(value)):.5f}"


def parse_new_entries(raw: str):
    """Rozparsza wartość parametru -n: NAZWA1:KWOTA1|NAZWA2:KWOTA2."""
    parts = raw.split("|")
    if len(parts) != 2:
        raise ValueError(
            "Oczekiwano dokładnie dwóch wpisów w formacie NAZWA1:KWOTA1|NAZWA2:KWOTA2."
        )
    entries = []
    for part in parts:
        name_raw, sep, amount_raw = part.partition(":")
        if not sep:
            raise ValueError(f"Wpis {part!r} nie ma postaci NAZWA:KWOTA.")
        entries.append((validate_name(name_raw), parse_amount(amount_raw)))
    return tuple(entries)


_TRANS_DT_RE = re.compile(r"^\d{8} \d{2}:\d{2}:\d{2}\.\d{3}$")


def parse_trans_datetime(raw: str) -> str:
    """Waliduje wartość parametru -t w formacie [YYYYMMDD HH:mm:SS.ms]."""
    s = raw.strip()
    if not _TRANS_DT_RE.match(s):
        raise ValueError(
            "Parametr -t: oczekiwano formatu YYYYMMDD HH:mm:SS.ms (np. 20261001 01:01:00.000)."
        )
    return s


def print_total(conn, trans_dt=None) -> None:
    rows = fetch_total(conn, trans_dt)
    table = [("ITEM_NAME", "SUM(ITEM_AMOUNT)")]
    table += [(name, format_amount(total)) for name, total in rows]
    widths = [max(len(row[i]) for row in table) for i in range(2)]
    for left, right in table:
        print(f"{left:<{widths[0]}}  {right:>{widths[1]}}")


def run_gui(db_path: Path) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    class TransactionDialog(tk.Toplevel):
        def __init__(self, master, on_ok):
            super().__init__(master)
            self.title("TRANSACTION")
            self.resizable(False, False)
            self.transient(master)
            self.on_ok = on_ok

            body = ttk.Frame(self, padding=12)
            body.pack(fill="both", expand=True)

            def item_frame(title):
                frame = ttk.LabelFrame(body, text=title, padding=8)
                frame.pack(fill="x", pady=(0, 8))
                return frame

            self.name1 = self._add_field(item_frame("Item 1"), "Item name:", row=0)
            self.amount1 = self._add_field(item_frame("Item 1"), "Item amount:", row=1)
            self.name2 = self._add_field(item_frame("Item 2"), "Item name:", row=0)
            self.amount2 = self._add_field(item_frame("Item 2"), "Item amount:", row=1)

            buttons = ttk.Frame(self, padding=(12, 4, 12, 12))
            buttons.pack(fill="x")
            ok_btn = ttk.Button(buttons, text="OK", command=self._on_ok)
            cancel_btn = ttk.Button(buttons, text="CANCEL", command=self.destroy)
            ok_btn.pack(side="right", padx=(8, 0))
            cancel_btn.pack(side="right")

            self.bind("<Return>", lambda _e: self._on_ok())
            self.bind("<Escape>", lambda _e: self.destroy())
            self.name1.focus_set()
            self.grab_set()

        def _add_field(self, parent, label_text, row):
            ttk.Label(parent, text=label_text).grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=3
            )
            entry = tk.Entry(parent, width=28)
            entry.grid(row=row, column=1, pady=3)
            parent.columnconfigure(1, weight=1)
            return entry

        def _on_ok(self):
            try:
                name1 = validate_name(self.name1.get())
                amount1 = parse_amount(self.amount1.get())
                name2 = validate_name(self.name2.get())
                amount2 = parse_amount(self.amount2.get())
            except ValueError as exc:
                messagebox.showerror("TRANSACTION", str(exc))
                return
            self.on_ok(name1, amount1, name2, amount2)
            self.destroy()

    class App(tk.Tk):
        def __init__(self, db_path):
            super().__init__()
            self.title("OPERATIONS")
            self.geometry("760x480")
            self.db = connect(db_path)
            self._build()
            self.refresh()
            self.protocol("WM_DELETE_WINDOW", self._on_close)

        def _make_tree(self, parent, columns):
            container = ttk.Frame(parent)
            container.pack(fill="both", expand=True)
            tree = ttk.Treeview(container, columns=columns, show="headings")
            vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
            hsb = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            container.rowconfigure(0, weight=1)
            container.columnconfigure(0, weight=1)
            return tree

        def _build(self):
            notebook = ttk.Notebook(self)
            notebook.pack(fill="both", expand=True, padx=8, pady=8)

            tab_total = ttk.Frame(notebook)
            tab_list = ttk.Frame(notebook)
            notebook.add(tab_total, text="TOTAL")
            notebook.add(tab_list, text="LIST")

            btn_row = ttk.Frame(tab_total)
            btn_row.pack(fill="x", padx=4, pady=(4, 0))
            self.btn_transaction = ttk.Button(
                btn_row, text="TRANSACTION", command=self._open_transaction
            )
            self.btn_transaction.pack(side="left")

            self.tree_total = self._make_tree(tab_total, ("item_name", "sum"))
            self.tree_total.heading("item_name", text="ITEM_NAME")
            self.tree_total.heading("sum", text="SUM(ITEM_AMOUNT)")
            self.tree_total.column("item_name", width=240, anchor="w")
            self.tree_total.column("sum", width=160, anchor="e")

            self.tree_list = self._make_tree(
                tab_list, ("id", "item_name", "amount", "trans_date_time")
            )
            self.tree_list.heading("id", text="ID")
            self.tree_list.heading("item_name", text="ITEM_NAME")
            self.tree_list.heading("amount", text="ITEM_AMOUNT")
            self.tree_list.heading("trans_date_time", text="TRANS DATE TIME")
            self.tree_list.column("id", width=70, anchor="e")
            self.tree_list.column("item_name", width=240, anchor="w")
            self.tree_list.column("amount", width=160, anchor="e")
            self.tree_list.column("trans_date_time", width=190, anchor="w")

        def refresh(self):
            for tree in (self.tree_total, self.tree_list):
                tree.delete(*tree.get_children())
            for name, total in fetch_total(self.db):
                self.tree_total.insert("", "end", values=(name, format_amount(total)))
            for row_id, name, amount, trans_dt in fetch_list(self.db):
                self.tree_list.insert(
                    "", "end", values=(row_id, name, format_amount(amount), trans_dt)
                )

        def _open_transaction(self):
            TransactionDialog(self, on_ok=self._commit)

        def _commit(self, n1, a1, n2, a2):
            insert_operations(self.db, n1, a1, n2, a2)
            self.refresh()

        def _on_close(self):
            self.db.close()
            self.destroy()

    root = App(db_path)
    root.mainloop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.py",
        description=(
            "Aplikacja OPERATIONS — GUI do wprowadzania wpisów; "
            "-l wypisuje dane z zakładki TOTAL na stdout, -n dodaje wpisy z linii komend."
        ),
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="wypisz wynik zapytania TOTAL na stdout i zakończ (bez GUI)",
    )
    parser.add_argument(
        "-n", "--new", metavar="NAZWA1:KWOTA1|NAZWA2:KWOTA2",
        help=(
            "dodaj dwa wpisy podane w formacie NAZWA1:KWOTA1|NAZWA2:KWOTA2 "
            "(np. ITM1:-100.2|ITM2:23) i zakończ (bez GUI)"
        ),
    )
    parser.add_argument(
        "-t", "--trans-date-time", metavar="YYYYMMDD HH:mm:SS.ms",
        help=(
            "do użycia z -l: wypisz TOTAL tylko dla wpisów, dla których "
            "TRANS_DATE_TIME <= podana wartość (np. \"20261001 01:01:00.000\")"
        ),
    )
    parser.add_argument(
        "-d", "--db", type=Path, default=DEFAULT_DB, metavar="SCIEZKA",
        help=f"plik bazy SQLite (domyślnie: {DEFAULT_DB.name} obok skryptu)",
    )
    args = parser.parse_args(argv)

    if args.trans_date_time is not None and not args.list:
        print("Opcja -t wymaga opcji -l.", file=sys.stderr)
        return 1

    try:
        if args.new is not None or args.list:
            conn = connect(args.db)
            try:
                if args.new is not None:
                    (n1, a1), (n2, a2) = parse_new_entries(args.new)
                    insert_operations(conn, n1, a1, n2, a2)
                    if not args.list:
                        print("Dodano 2 wpisy.")
                if args.list:
                    trans_dt = (
                        parse_trans_datetime(args.trans_date_time)
                        if args.trans_date_time is not None else None
                    )
                    print_total(conn, trans_dt)
            finally:
                conn.close()
            return 0
        run_gui(args.db)
    except ValueError as exc:
        print(f"Błąd parametrów: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Błąd bazy danych: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
