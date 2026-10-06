"""
build_kpi_graph.py  (auswahl-basiert, PyVis-Ausgabe)
----------------------------------------------------
Der Nutzer liefert NUR Messwerte. Das Programm sucht selbst alle KPIs aus dem
Katalog, die aus diesen Messwerten berechenbar sind, und zeigt den Graphen in
einem interaktiven PyVis-Fenster (HTML im Browser).

  Resources/kpi_catalogue.csv : ALLE KPI-Vorlagen (Formel, Dimension, Ziel) - der Katalog. Modifizierbar/Anzeigbar mit kpi_catalogue_gui.py
  Resources/structure.csv     : Fabrik-Struktur (Factory / Line / Cell)
  Resources/measure_data.csv  : NUR die eingereichten Messwerte (je Messgroesse UND Ort)

Auswahl-Regeln:
  1) RELEVANZ: Ein KPI kommt in den Graphen, wenn ALLE seine Eingaenge aus den
     eingereichten Messwerten berechenbar sind (Sub-KPIs rekursiv).
  2) FAKTOR (Standard 0.9): Eine Kante "Kind -> Eltern" wird nur behalten, wenn das
     Kind den Eltern-KPI um mindestens (1 - Faktor) = 10% beeinflusst. Der Einfluss
     wird als ELASTIZITAET gemessen (kleine Aenderung des Kindes -> relative
     Aenderung des Eltern-KPI). Messgroessen/Zweige unter 10% werden weggelassen.

  Der berechnete KPI-Wert nutzt weiterhin ALLE Eingaben (exakt); der Faktor blendet
  nur schwache Einfluesse aus der Darstellung aus. Ausgeblendeter Anteil wird je KPI
  als Tooltip und im Log vermerkt.
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

from pyvis.network import Network      # pip install pyvis

BASE = Path(__file__).resolve().parent
RES = BASE / "Resources"
OUT = BASE / "Output"
OUT.mkdir(exist_ok=True)

# --- Konfiguration ---------------------------------------------------------
DIMENSIONS = {
    "Environmental": "#1B9E77",   # gruen
    "Economic":      "#D95F02",   # orange
    "Social":        "#7570B3",   # violett
}
FALLBACK_COLOR = "#999999"
LEVEL_LIGHTEN = 0.30              # pro Baum-Ebene heller

RELEVANCE_FACTOR = 0.9           # 0.9 => Einfluesse < 10% werden ausgeblendet
MIN_IMPACT = 1 - RELEVANCE_FACTOR
EPS = 1e-3                        # kleine Stoerung fuer die numerische Elastizitaet

MAX_SIZE = 60.0                  # groesster Knoten
MIN_RATIO = 0.25                 # kleinster Knoten = 25% des groessten

# Rahmenfarbe des Knotens = Zielstatus
STATUS_BORDER = {"reached": "#2E7D32", "missed": "#C62828", "no target": "#9E9E9E"}

OPEN_IN_BROWSER = True           # HTML nach dem Erzeugen automatisch oeffnen


# --- Hilfen ----------------------------------------------------------------
def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % rgb


def lighten(rgb, t):
    return tuple(int(round(c + (255 - c) * t)) for c in rgb)


# --- Eingaben --------------------------------------------------------------
catalogue = {c["kpi_id"]: c for c in read(RES / "kpi_catalogue.csv")}
structure = {s["node_id"]: s for s in read(RES / "structure.csv")}

values = defaultdict(dict)                     # values[loc][measure_id] = Zahl
for row in read(RES / "measure_data.csv"):
    values[row["location_id"]][row["kpi_id"]] = float(row["value"])

REF = re.compile(r"\{([^}]+)\}")

# Layout-Hierarchie aus den Formeln ableiten:
# Wer {X} in seiner Formel referenziert, gilt als Elternteil von X. Bei mehreren
# referenzierenden KPIs entscheidet das erste - nur fuer Positionierung/Ebene,
# nicht fuer die Kanten (die kommen weiterhin komplett aus den Formeln).
parent_of = {kid: "" for kid in catalogue}
for _pid, _c in catalogue.items():
    for _ref in REF.findall(_c.get("formula") or ""):
        if _ref in parent_of and not parent_of[_ref]:
            parent_of[_ref] = _pid


# --- Berechenbarkeit & Wert (je Ort) --------------------------------------
# kid = kpi-id, loc = location (e.g. F1-L1-C1), memo zeigt, ob die jeweilige ID berechenbar ist.
def computable(kid, loc, memo):
    if kid in memo:
        return memo[kid]
    node = catalogue.get(kid)
    if node is None:                           # ID unbekannt -> nicht berechenbar
        memo[kid] = False
        return False
    if not node["formula"]:                    # Messgroesse: berechenbar, wenn eingereicht
        memo[kid] = kid in values[loc]
        return memo[kid]
    memo[kid] = all(computable(r, loc, memo) for r in REF.findall(node["formula"]))
    return memo[kid]

# cache zeigt den Zahlenwert als Zwischenspeicher fuer die jeweilige ID
def value(kid, loc, cache):
    if kid in cache:
        return cache[kid]
    node = catalogue[kid]
    if not node["formula"]:
        cache[kid] = values[loc][kid]
        return cache[kid]
    expr = REF.sub(lambda m: repr(value(m.group(1), loc, cache)), node["formula"])
    cache[kid] = float(eval(expr, {"__builtins__": {}}, {}))
    return cache[kid]


# Einfluss Kindknoten -> Elternknoten
def elasticity(parent, ref, loc, cache):
    """Relative Aenderung des Eltern-KPI bei kleiner Aenderung des Kindes 'ref'."""
    base = value(parent, loc, cache)
    cv = value(ref, loc, cache)
    if cv == 0 or base == 0:
        return 0.0
    bumped = cv * (1 + EPS)
    expr = REF.sub(lambda m: repr(bumped if m.group(1) == ref else value(m.group(1), loc, cache)),
                   catalogue[parent]["formula"])
    newp = float(eval(expr, {"__builtins__": {}}, {}))
    return abs((newp - base) / base / EPS)


# --- Auswahl je Ort: relevante, berechenbare KPIs + starke Kanten ----------
def select(loc):
    memo, cache = {}, {}
    # "Top"-KPIs: berechenbar und ohne berechenbaren Eltern (Wurzeln der Auswahl)
    tops = [k for k, c in catalogue.items()
            if c["formula"] and computable(k, loc, memo)
            and (not parent_of[k] or not computable(parent_of[k], loc, memo))]

    kept, edges, pruned = set(), [], []        # pruned: (ref, parent, share) unter der Schwelle
    def keep(kid):
        kept.add(kid)
        c = catalogue[kid]
        if not c["formula"]:
            return
        value(kid, loc, cache)                 # Cache fuellen
        for ref in REF.findall(c["formula"]):
            if not computable(ref, loc, memo):
                continue
            e = elasticity(kid, ref, loc, cache)
            if e >= MIN_IMPACT:
                edges.append((ref, kid, e))
                if ref not in kept:
                    keep(ref)
            else:
                pruned.append((ref, kid, e))   # < 10% -> weglassen
    for t in tops:
        keep(t)
    return kept, edges, pruned, cache


# --- Alles zusammenbauen ---------------------------------------------------
locations = list(values)
inst = {}                                      # (kid, loc) -> Attribute
kept_edges_all = []                            # (src, tgt, strength, loc)
pruned_all = defaultdict(list)                 # loc -> [(ref, parent, share)]
pos = {}
y_base = 0.0


def factory_of(locid):
    cur = structure[locid]
    while cur["parent"]:
        cur = structure[cur["parent"]]
    return cur["node_id"]


for loc in locations:
    kept, edges, pruned, cache = select(loc)
    pruned_all[loc] = pruned

    # Kinder je Eltern (nur behaltene Kanten) fuer Layout + measures_behind
    children = defaultdict(list)
    for ref, parent, e in edges:
        children[parent].append(ref)
    roots = [k for k in kept if not parent_of[k] or parent_of[k] not in kept]

    def level(kid):
        lvl, cur = 0, kid
        while parent_of[cur] in kept:
            lvl, cur = lvl + 1, parent_of[cur]
        return lvl

    def measures_behind(kid):
        c = catalogue[kid]
        if not c["formula"] or kid not in kept:
            return 1 if not c["formula"] else 0
        leaves = 0
        for ch in children[kid]:
            leaves += 1 if not catalogue[ch]["formula"] else measures_behind(ch)
        return leaves

    # Layout: ein Baum je Wurzel, Ebene = x, Geschwister ueber y
    y_cursor = [y_base]

    def place(kid, depth):
        ch = children[kid]
        if not ch:
            y = y_cursor[0]
            y_cursor[0] += 90.0
        else:
            y = sum(place(c, depth + 1) for c in ch) / len(ch)
        pos[(kid, loc)] = (depth * -240.0, y)
        return y
    for r in roots:
        place(r, 0)
        y_cursor[0] += 60.0
    y_base = y_cursor[0] + 200.0

    # ausgeblendeter Anteil je Eltern (additiv, zur Transparenz)
    pruned_share = defaultdict(float)
    for ref, parent, e in pruned:
        p = value(parent, loc, cache)
        pruned_share[parent] += abs(value(ref, loc, cache) / p) if p else 0.0

    for kid in kept:
        c = catalogue[kid]
        val = value(kid, loc, cache)
        tgt = c["target"]
        if not tgt:
            st = "no target"
        else:
            ok = val <= float(tgt) if c["target_direction"] == "min" else val >= float(tgt)
            st = "reached" if ok else "missed"
        base = hex_to_rgb(DIMENSIONS.get(c["dimension"], FALLBACK_COLOR))
        inst[(kid, loc)] = {
            "value": val, "status": st,
            "color": rgb_to_hex(lighten(base, level(kid) * LEVEL_LIGHTEN)),
            "level": level(kid), "mbehind": measures_behind(kid),
            "pruned_share": round(pruned_share.get(kid, 0.0), 4),
        }
    for ref, parent, e in edges:
        kept_edges_all.append((ref, parent, round(min(e, 1.0), 4), loc))

# --- Groessen ueber alle behaltenen Knoten normieren -----------------------
if inst:
    counts = [d["mbehind"] for d in inst.values()]
    lo, hi = min(counts), max(counts)
    span = (hi - lo) or 1
    min_size = MAX_SIZE * MIN_RATIO
    for d in inst.values():
        d["size"] = min_size + (d["mbehind"] - lo) / span * (MAX_SIZE - min_size)

# ---------------------------------------------------------------------------
# PyVis-Ausgabe
# ---------------------------------------------------------------------------
net = Network(height="800px", width="100%", directed=True,
              bgcolor="#ffffff", font_color="#222222")
# feste Positionen aus unserem Baum-Layout; Knoten/Kanten bewegen sich nicht
net.toggle_physics(False)


def tooltip(kid, loc, d):
    c = catalogue[kid]
    lines = [f"<b>{c['label']}</b> ({kid})",
             f"Typ: {c['node_type']}",
             f"Dimension: {c['dimension']} / {c['subdimension']}",
             f"Wert: {d['value']:,.2f} {c['unit']}",
             f"Ort: {loc}  (Fabrik {factory_of(loc)})"]
    if c["formula"]:
        lines.append(f"Formel: {c['formula']}")
    if c["target"]:
        lines.append(f"Ziel: {c['target']} ({c['target_direction']}) &rarr; {d['status']}")
    if d["pruned_share"]:
        lines.append(f"ausgeblendeter Einfluss: {d['pruned_share']:.0%}")
    return "<br>".join(lines)


for (kid, loc), d in inst.items():
    c = catalogue[kid]
    x, y = pos[(kid, loc)]
    net.add_node(
        f"{kid}@{loc}",
        label=c["label"],
        title=tooltip(kid, loc, d),
        color={"background": d["color"],
               "border": STATUS_BORDER.get(d["status"], "#9E9E9E")},
        borderWidth=3,
        shape="dot",
        size=d["size"] / 2.0,                 # PyVis-Groesse etwas kleiner skaliert
        x=x, y=y, physics=False,
    )

for src, tgt, strength, loc in kept_edges_all:
    net.add_edge(
        f"{src}@{loc}", f"{tgt}@{loc}",
        value=strength,                        # Kantendicke ~ Einfluss
        title=f"Einfluss: {strength:.0%}",
        color=inst[(src, loc)]["color"],
        arrows="to",
    )

# kleine Legende als eigenstaendige Knoten (rechts oben, ohne Kanten)
if inst:
    lx = max(x for x, _ in pos.values()) + 260
    ly = min(y for _, y in pos.values())
    for i, (dim, col) in enumerate(DIMENSIONS.items()):
        net.add_node(f"_legend_dim_{i}", label=dim, shape="dot", size=12,
                     color={"background": col, "border": col},
                     x=lx, y=ly + i * 55, physics=False, font={"size": 14})
    for j, (stat, col) in enumerate(STATUS_BORDER.items()):
        net.add_node(f"_legend_stat_{j}", label=f"Ziel: {stat}", shape="dot", size=12,
                     color={"background": "#ffffff", "border": col},
                     borderWidth=3, x=lx, y=ly + (len(DIMENSIONS) + j) * 55 + 30,
                     physics=False, font={"size": 14})

out_html = OUT / "kpi_model.html"
net.write_html(str(out_html), open_browser=False, notebook=False)

if OPEN_IN_BROWSER:
    import webbrowser
    webbrowser.open(out_html.as_uri())

# --- Log -------------------------------------------------------------------
from datetime import datetime
log = [f"Lauf: {datetime.now():%Y-%m-%d %H:%M:%S}",
       f"Relevanz-Faktor {RELEVANCE_FACTOR} (Einfluss-Schwelle {MIN_IMPACT:.0%})",
       f"{len(inst)} Knoten, {len(kept_edges_all)} Kanten  ->  {out_html}",
       ""]
for loc in locations:
    log.append(f"{loc}  ({factory_of(loc)})   eingereichte Messwerte: {len(values[loc])}")
    for kid in [k for (k, l) in inst if l == loc]:
        c = catalogue[kid]
        if c["node_type"] != "Measure":
            d = inst[(kid, loc)]
            extra = f"  (ausgeblendet: {d['pruned_share']:.0%})" if d["pruned_share"] else ""
            log.append(f"  {c['label']:34} = {d['value']:>12,.2f} {c['unit']:<6} "
                       f"[{d['status']}]{extra}")
    if pruned_all[loc]:
        log.append("  ausgeblendete Einfluesse (< Schwelle):")
        for ref, parent, e in pruned_all[loc]:
            log.append(f"    {catalogue[ref]['label']} -> {catalogue[parent]['label']}: {e:.1%}")
    log.append("")
(OUT / "log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
print(f"Fertig. Graph: {out_html}  -  Details siehe {OUT / 'log.txt'}")
