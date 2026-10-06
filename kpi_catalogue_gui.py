"""
kpi_catalogue_gui.py
--------------------
GUI zum Ansehen, Hinzufuegen und Bearbeiten des KPI-Katalogs.

(Tkinter gehoert zur Standardbibliothek. Falls 'import tkinter' auf macOS mit
 Homebrew-Python fehlschlaegt:  brew install python-tk )
"""

import csv
import re
import sys
import subprocess
from pathlib import Path

# ===========================================================================
# BENUTZER  (vorerst einfach hier als String setzen)
# ===========================================================================
AUTHOR = "Admin"        # "Admin" | "User" | irgendein Name, z.B. "Alina"

BASE = Path(__file__).resolve().parent
CATALOGUE = BASE / "Resources" / "kpi_catalogue.csv"
BUILD_SCRIPT = BASE / "build_kpi_graph.py"

# Spalten in fester Reihenfolge (author + comment neu; vom Hauptprogramm ignoriert)
COLUMNS = ["kpi_id", "label", "node_type", "dimension", "subdimension",
           "unit", "formula", "target", "target_direction", "author", "comment"]

NODE_TYPES = ["KPI", "Sub-KPI", "Measure"]
DIMENSIONS = ["Environmental", "Economic", "Social"]
DIRECTIONS = ["", "min", "max"]

DEFAULT_AUTHOR = "Standard"          # Autor fuer bereits vorhandene Eintraege

# In Formeln erlaubte Zeichen NACH Entfernen der {ID}-Tokens
ALLOWED_FORMULA_CHARS = set("+-*/() \t0123456789.")   # inkl. Zahlen/Konstanten (z.B. 2*{COST-01})
_ID_TOKEN = re.compile(r"\{([^{}]*)\}")


# ===========================================================================
# DATENSCHICHT  (ohne GUI -> unabhaengig testbar)
# ===========================================================================
def load_catalogue(path=CATALOGUE):
    """CSV lesen -> Liste von dicts. Fehlender author -> 'Standard'."""
    if not Path(path).exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            d = {c: (row.get(c) or "").strip() for c in COLUMNS}
            if not d["author"]:
                d["author"] = DEFAULT_AUTHOR
            rows.append(d)
    return rows


def save_catalogue(rows, path=CATALOGUE):
    """Liste von dicts -> CSV (Semikolon). csv quotet Felder mit ';' automatisch."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r.get(c, "") for c in COLUMNS])


def validate_formula(formula, known_ids):
    """Formel pruefen. Reihenfolge: erst IDs (muessen existieren), dann Syntax.
    known_ids = Menge bereits vorhandener IDs (ohne die Zeile selbst).
    Rueckgabe: Liste von Fehlermeldungen (leer = ok)."""
    f = (formula or "").strip()
    if not f:
        return []
    refs = _ID_TOKEN.findall(f)
    if any(r.strip() == "" for r in refs):
        return ["Leere Referenz {} in der Formel."]
    # 1) jede referenzierte ID muss bereits existieren
    unknown = sorted({r for r in refs if r not in known_ids})
    if unknown:
        return ["Unbekannte ID(s) in der Formel: " + ", ".join(unknown)
                + "  (nur bereits vorhandene IDs erlaubt)"]
    # 2) Syntax: nach Entfernen der {IDs} nur erlaubte Zeichen
    rest = _ID_TOKEN.sub(" ", f)
    bad = sorted({ch for ch in rest if ch not in ALLOWED_FORMULA_CHARS})
    if bad:
        return ["Nicht erlaubte Zeichen in der Formel: " + " ".join(repr(c) for c in bad)
                + "  (erlaubt: {IDs}, Zahlen, + - * / und Klammern)"]
    if "**" in rest or "//" in rest:
        return ["Nur einfache Operatoren erlaubt (kein ** oder //)."]
    # 3) Ausfuehrbarkeit: jede ID durch (1.0) ersetzen und sicher auswerten
    test = _ID_TOKEN.sub("(1.0)", f)
    try:
        eval(test, {"__builtins__": {}}, {})
    except Exception:
        return ["Formel ist nicht ausfuehrbar - bitte Klammern/Operatoren pruefen."]
    return []


def validate_common(row, others):
    """Basisregeln (ohne Formel-Inhaltspruefung). 'others' = alle anderen Zeilen."""
    errors = []
    kid = row["kpi_id"].strip()
    if not kid:
        errors.append("kpi_id darf nicht leer sein.")
    if ";" in kid or " " in kid:
        errors.append("kpi_id darf kein Semikolon oder Leerzeichen enthalten.")
    if kid and any(r["kpi_id"] == kid for r in others):
        errors.append(f"kpi_id '{kid}' existiert bereits.")
    if not row["label"].strip():
        errors.append("label darf nicht leer sein.")
    if row["node_type"] not in NODE_TYPES:
        errors.append(f"node_type muss eines von {NODE_TYPES} sein.")

    formula = row["formula"].strip()
    if row["node_type"] == "Measure" and formula:
        errors.append("Eine Messgroesse (Measure) darf keine Formel haben.")
    if row["node_type"] in ("KPI", "Sub-KPI") and not formula:
        errors.append("Ein KPI / Sub-KPI braucht eine Formel (z.B. {A}+{B}).")

    if row["dimension"] and row["dimension"] not in DIMENSIONS:
        errors.append(f"dimension muss eines von {DIMENSIONS} sein.")

    tgt = row["target"].strip()
    if tgt:
        try:
            float(tgt)
        except ValueError:
            errors.append("target muss eine Zahl sein (oder leer).")
        if row["target_direction"] not in ("min", "max"):
            errors.append("target_direction muss 'min' oder 'max' sein, wenn ein Ziel gesetzt ist.")
    return errors


def validate_new(row, rows):
    """Beim HINZUFUEGEN: Basisregeln + Formelpruefung (Formel wird immer geprueft)."""
    errs = validate_common(row, rows)
    if row["node_type"] in ("KPI", "Sub-KPI") and row["formula"].strip():
        errs += validate_formula(row["formula"], {r["kpi_id"] for r in rows})
    return errs


def validate_edit(row, others, changed_col):
    """Beim BEARBEITEN einer Zelle: Basisregeln immer; Formelpruefung nur, wenn
    die Formel selbst (oder node_type) geaendert wurde -> so blockiert z.B. eine
    Kommentar-Aenderung keine (evtl. alte) Formel."""
    errs = validate_common(row, others)
    if changed_col in ("formula", "node_type") and \
            row["node_type"] in ("KPI", "Sub-KPI") and row["formula"].strip():
        errs += validate_formula(row["formula"], {r["kpi_id"] for r in others})
    return errs


# ===========================================================================
# ROLLEN / BERECHTIGUNGEN  (pure Funktionen -> testbar)
# ===========================================================================
def role_of(author):
    if author == "Admin":
        return "admin"
    if author == "User":
        return "user"
    return "named"


def can_add(role):
    return role in ("admin", "named")


def can_use_undo(role):
    return role in ("admin", "named")


def can_delete_row(role, me, row_author):
    if role == "admin":
        return True
    if role == "named":
        return row_author == me
    return False                       # user: kein Loeschen


def can_edit_cell(role, me, row_author, col):
    if col == "comment":
        return True                    # Kommentare darf jeder
    if col == "author":
        return False                   # Autor wird automatisch verwaltet
    if role == "admin":
        return True
    if role == "named":
        return row_author == me
    return False                       # user: nur comment


# ===========================================================================
# GUI-SCHICHT  (Tkinter)
# ===========================================================================
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox

    ROLE = role_of(AUTHOR)
    rows = load_catalogue()
    undo_stack = []                    # Liste von Snapshots (je Liste von dicts)

    root = tk.Tk()
    root.title("KPI-Katalog")
    root.geometry("1250x540")

    # --- Werkzeugleiste ----------------------------------------------------
    bar = ttk.Frame(root, padding=(8, 6))
    bar.pack(fill="x")
    ttk.Label(bar, text=f"Angemeldet als: {AUTHOR}  [{ROLE}]",
              font=("", 10, "bold")).pack(side="left", padx=(0, 12))
    ttk.Label(bar, text=str(CATALOGUE), foreground="#666").pack(side="right")

    # --- Tabelle -----------------------------------------------------------
    wrap = ttk.Frame(root)
    wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    tree = ttk.Treeview(wrap, columns=COLUMNS, show="headings", height=18)
    widths = {"kpi_id": 85, "label": 190, "node_type": 75, "dimension": 105,
              "subdimension": 100, "unit": 80, "formula": 190, "target": 60,
              "target_direction": 105, "author": 85, "comment": 220}
    for c in COLUMNS:
        tree.heading(c, text=c)
        tree.column(c, width=widths.get(c, 100), anchor="w", stretch=False)
    ysb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
    xsb = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    ysb.grid(row=0, column=1, sticky="ns")
    xsb.grid(row=1, column=0, sticky="ew")
    wrap.rowconfigure(0, weight=1)
    wrap.columnconfigure(0, weight=1)

    def refresh():
        tree.delete(*tree.get_children())
        for r in rows:
            tree.insert("", "end", values=[r.get(c, "") for c in COLUMNS])

    # --- Undo --------------------------------------------------------------
    undo_btn = None

    def update_undo_button():
        if undo_btn is not None:
            undo_btn.config(state="normal" if undo_stack else "disabled")

    def snapshot():
        undo_stack.append([dict(r) for r in rows])
        if len(undo_stack) > 50:
            undo_stack.pop(0)
        update_undo_button()

    def do_undo():
        if not undo_stack:
            return
        rows[:] = undo_stack.pop()
        save_catalogue(rows)
        refresh()
        update_undo_button()

    def row_by_kid(kid):
        return next((r for r in rows if r["kpi_id"] == kid), None)

    # --- Dialog: neuen KPI hinzufuegen ------------------------------------
    def _apply_field_rules(entries):
        """Measure -> formula/target/target_direction sperren; kein target ->
        target_direction sperren. Gesperrte Felder werden geleert."""
        is_measure = entries["node_type"].get() == "Measure"

        def set_state(widget, enabled):
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                widget.configure(state="normal" if enabled else "disabled")

        if is_measure:
            entries["formula"].delete(0, "end")
        set_state(entries["formula"], not is_measure)

        if is_measure:
            entries["target"].delete(0, "end")
        set_state(entries["target"], not is_measure)

        has_target = bool(entries["target"].get().strip()) and not is_measure
        if not has_target:
            entries["target_direction"].set("")
        set_state(entries["target_direction"], has_target)

    def add_dialog():
        win = tk.Toplevel(root)
        win.title("Add KPI")
        win.geometry("560x500")
        win.transient(root)
        win.grab_set()

        entries = {}
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        for i, col in enumerate(COLUMNS):
            ttk.Label(frm, text=col).grid(row=i, column=0, sticky="w", pady=3)
            if col == "node_type":
                w = ttk.Combobox(frm, values=NODE_TYPES, state="readonly")
                w.set("KPI")
            elif col == "dimension":
                w = ttk.Combobox(frm, values=DIMENSIONS, state="readonly")
                w.set("Economic")
            elif col == "target_direction":
                w = ttk.Combobox(frm, values=DIRECTIONS, state="readonly")
                w.set("")
            elif col == "author":
                w = ttk.Entry(frm)
                w.insert(0, AUTHOR)
                w.configure(state="disabled")     # Autor automatisch = aktueller Benutzer
            else:
                w = ttk.Entry(frm)
            w.grid(row=i, column=1, sticky="ew", pady=3)
            entries[col] = w
        frm.columnconfigure(1, weight=1)

        entries["node_type"].bind("<<ComboboxSelected>>", lambda e: _apply_field_rules(entries))
        entries["target"].bind("<KeyRelease>", lambda e: _apply_field_rules(entries))
        _apply_field_rules(entries)

        hint = ttk.Label(frm, text="Formel: z.B. 2*({ECO-11}+{ECO-12})/{ECO-10}",
                         foreground="#666")
        hint.grid(row=len(COLUMNS), column=0, columnspan=2, sticky="w", pady=(8, 0))

        def on_save():
            new = {c: entries[c].get().strip() for c in COLUMNS}
            new["author"] = AUTHOR                 # Autor immer = aktueller Benutzer
            errs = validate_new(new, rows)
            if errs:
                messagebox.showerror("Ungueltige Eingabe", "\n".join(errs), parent=win)
                return
            snapshot()
            rows.append(new)
            save_catalogue(rows)
            refresh()
            win.destroy()

        btns = ttk.Frame(win, padding=(12, 0, 12, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="Save", command=on_save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # --- Loeschen ----------------------------------------------------------
    def delete_selected():
        sel = tree.selection()
        if not sel:
            return
        chosen = [tree.set(s, "kpi_id") for s in sel]
        deletable, blocked = [], []
        for kid in chosen:
            r = row_by_kid(kid)
            if r is None:
                continue
            if can_delete_row(ROLE, AUTHOR, r.get("author", DEFAULT_AUTHOR)):
                deletable.append(kid)
            else:
                blocked.append(kid)
        if not deletable:
            messagebox.showinfo("Keine Berechtigung",
                                "Du kannst nur eigene Eintraege loeschen.")
            return
        msg = f"{len(deletable)} Eintrag/e loeschen?"
        if blocked:
            msg += f"\n(Nicht loeschbar, da nicht von dir: {', '.join(blocked)})"
        if not messagebox.askyesno("Loeschen", msg):
            return
        snapshot()
        rows[:] = [r for r in rows if r["kpi_id"] not in set(deletable)]
        save_catalogue(rows)
        refresh()

    def reload_file():
        rows[:] = load_catalogue()
        undo_stack.clear()
        update_undo_button()
        refresh()

    def build_graph():
        if not BUILD_SCRIPT.exists():
            messagebox.showinfo("Build graph", f"Nicht gefunden: {BUILD_SCRIPT}")
            return
        proc = subprocess.run([sys.executable, str(BUILD_SCRIPT)],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            messagebox.showinfo("Build graph", proc.stdout.strip() or "Fertig.")
        else:
            messagebox.showerror("Build graph - Fehler", proc.stderr.strip()[-1500:])

    # --- Doppelklick: Zelle bearbeiten (mit Rechten) -----------------------
    def on_double_click(event):
        if tree.identify("region", event.x, event.y) != "cell":
            return
        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id or not col_id:
            return
        col_name = COLUMNS[int(col_id[1:]) - 1]
        kid = tree.set(row_id, "kpi_id")
        r = row_by_kid(kid)
        if r is None:
            return
        row_author = r.get("author", DEFAULT_AUTHOR)

        if not can_edit_cell(ROLE, AUTHOR, row_author, col_name):
            messagebox.showinfo(
                "Keine Berechtigung",
                f"Feld '{col_name}' darf von dir hier nicht geaendert werden.\n"
                "(User duerfen nur Kommentare; benannte Nutzer nur eigene Eintraege.)")
            return

        x, y, w, h = tree.bbox(row_id, col_id)
        cur = tree.set(row_id, col_name)
        if col_name == "node_type":
            editor = ttk.Combobox(tree, values=NODE_TYPES, state="readonly")
        elif col_name == "dimension":
            editor = ttk.Combobox(tree, values=DIMENSIONS, state="readonly")
        elif col_name == "target_direction":
            editor = ttk.Combobox(tree, values=DIRECTIONS, state="readonly")
        else:
            editor = ttk.Entry(tree)
        editor.place(x=x, y=y, width=w, height=h)
        if isinstance(editor, ttk.Combobox):
            editor.set(cur)
        else:
            editor.insert(0, cur)
            editor.select_range(0, "end")
        editor.focus_set()

        def commit(_=None):
            newval = editor.get().strip()
            editor.destroy()
            if newval == cur:
                return
            idx = next((n for n, rr in enumerate(rows) if rr["kpi_id"] == kid), None)
            if idx is None:
                return
            candidate = dict(rows[idx])
            candidate[col_name] = newval
            others = [rr for n, rr in enumerate(rows) if n != idx]
            errs = validate_edit(candidate, others, col_name)
            if errs:
                messagebox.showerror("Ungueltige Aenderung", "\n".join(errs))
                return
            snapshot()
            rows[idx] = candidate
            save_catalogue(rows)
            refresh()

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda e: editor.destroy())

    tree.bind("<Double-1>", on_double_click)

    # --- Knoepfe je nach Rolle --------------------------------------------
    if can_add(ROLE):
        ttk.Button(bar, text="Add KPI...", command=add_dialog).pack(side="left")
    if ROLE in ("admin", "named"):
        ttk.Button(bar, text="Delete selected", command=delete_selected).pack(side="left", padx=6)
    if can_use_undo(ROLE):
        undo_btn = ttk.Button(bar, text="Undo", command=do_undo, state="disabled")
        undo_btn.pack(side="left", padx=(0, 6))
    ttk.Button(bar, text="Reload", command=reload_file).pack(side="left")
    ttk.Button(bar, text="Build graph", command=build_graph).pack(side="left", padx=6)

    if ROLE == "user":
        ttk.Label(bar, text="(nur Lesen + Kommentare)", foreground="#a00").pack(side="left", padx=8)

    refresh()
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
