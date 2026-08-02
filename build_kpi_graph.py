"""
build_kpi_graph.py
-----------------------------
Kombiniert drei generische CSVs zu einem konkreten Node-Link-Graphen (GEXF).

  Resources/kpi_catalogue.csv : KPI-Vorlagen (Formel, Dimension, Zielwert)
  Resources/structure.csv     : Fabrik-Struktur (Factory / Line / Cell)
  Resources/measure_data.csv  : Messwert je Messgröße UND Ort

Stuktur:
  Ein KPI ist eine VORLAGE. Jeder Ort mit Messdaten bekommt eine eigene
  INSTANZ des kompletten KPI-Baums. Knoten-ID = <kpi_id>@<location_id>.
"""

import csv
import re
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

BASE = Path(__file__).resolve().parent
RES = BASE / "Resources"
OUT = BASE / "Output"
OUT.mkdir(exist_ok=True)

# --- Konfiguration: drei Nachhaltigkeitssäulen --
DIMENSIONS = {
    "Environmental": "#1B9E77",   # grün
    "Economic":      "#D95F02",   # orange
    "Social":        "#7570B3",   # violett
}
FALLBACK_COLOR = "#999999"
LEVEL_LIGHTEN = 0.30              # pro Baum-Ebene heller
MAX_SIZE = 60.0                   # größter Knoten
MIN_RATIO = 0.25                  # kleinster Knoten = 25% des größten


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lighten(rgb, t):
    return tuple(int(round(c + (255 - c) * t)) for c in rgb)


# ---------------------------------------------------------------------------
# 1) Eingaben laden
# ---------------------------------------------------------------------------
catalogue = {c["kpi_id"]: c for c in read(RES / "kpi_catalogue.csv")}
structure = {s["node_id"]: s for s in read(RES / "structure.csv")}

# Messwerte nach Ort gruppieren:  values[location_id][kpi_id] = Zahl
values = defaultdict(dict)
for row in read(RES / "measure_data.csv"):
    values[row["location_id"]][row["kpi_id"]] = float(row["value"])

REF = re.compile(r"\{([^}]+)\}")


# ---------------------------------------------------------------------------
# 2) Formeln je Ort auflösen
# ---------------------------------------------------------------------------
def resolve(kid, loc, cache, stack=()):
    if kid in cache:
        return cache[kid]
    node = catalogue[kid]
    if not node["formula"]:                       # Messgröße -> Wert aus measure_data
        if kid not in values[loc]:
            raise ValueError(f"Messwert fehlt: {kid} @ {loc}")
        cache[kid] = values[loc][kid]
        return cache[kid]
    if kid in stack:
        raise ValueError(f"Zirkuläre Formel: {' -> '.join(stack + (kid,))}")
    expr = REF.sub(lambda m: repr(resolve(m.group(1), loc, cache, stack + (kid,))),
                   node["formula"])
    cache[kid] = float(eval(expr, {"__builtins__": {}}, {}))
    return cache[kid]


# ---------------------------------------------------------------------------
# 3) Struktur der Vorlage: Baum (parent) + Einfluss (Formel)
# ---------------------------------------------------------------------------
kids = defaultdict(list)                           # parent-Spalte -> Layout-Baum
for kid, c in catalogue.items():
    if c["parent"]:
        kids[c["parent"]].append(kid)
roots = [k for k, c in catalogue.items() if not c["parent"]]


def level(kid):
    lvl, cur = 0, catalogue[kid]
    while cur["parent"]:
        lvl, cur = lvl + 1, catalogue[cur["parent"]]
    return lvl


def feeding_measures(kid):                         # Messgrößen, die (via Formel) einfliessen
    node = catalogue[kid]
    if not node["formula"]:
        return {kid}
    s = set()
    for ref in REF.findall(node["formula"]):
        s |= feeding_measures(ref)
    return s


mcount = {kid: (0 if not c["formula"] else len(feeding_measures(kid)))
          for kid, c in catalogue.items()}

# Größe: Anzahl einfließender Messgrößen linear auf [min_size, MAX_SIZE]
lo, hi = min(mcount.values()), max(mcount.values())
span = (hi - lo) or 1
min_size = MAX_SIZE * MIN_RATIO
size = {kid: min_size + (mcount[kid] - lo) / span * (MAX_SIZE - min_size)
        for kid in catalogue}


def factory_of(locid):                             # oberster Knoten
    cur = structure[locid]
    while cur["parent"]:
        cur = structure[cur["parent"]]
    return cur["node_id"]


# ---------------------------------------------------------------------------
# 4) Instanzen erzeugen: pro Ort mit Daten ein kompletter KPI-Baum
# ---------------------------------------------------------------------------
inst = {}                                          # (kid, loc) -> dict mit value/status/pos...
pos = {}
y_base = 0.0
locations = list(values)                           # Orte, die Messdaten haben


def place(kid, loc, depth, y_cursor):
    ch = kids[kid]
    if not ch:
        y = y_cursor[0]
        y_cursor[0] += 90.0
    else:
        ys = [place(c, loc, depth + 1, y_cursor) for c in ch]
        y = sum(ys) / len(ys)
    pos[(kid, loc)] = (depth * -240.0, y)          # Wurzel rechts, Messgrößen links
    return y


for loc in locations:
    cache = {}
    for kid in catalogue:
        resolve(kid, loc, cache)                   # Werte fuer diesen Ort berechnen
    y_cursor = [y_base]
    for r in roots:
        place(r, loc, 0, y_cursor)
        y_cursor[0] += 60.0
    y_base = y_cursor[0] + 200.0                    # Abstand zwischen Orten

    for kid, c in catalogue.items():
        val = cache[kid]
        tgt = c["target"]
        if not tgt:
            st = "no target"
        else:
            ok = val <= float(tgt) if c["target_direction"] == "min" else val >= float(tgt)
            st = "reached" if ok else "missed"
        base = hex_to_rgb(DIMENSIONS.get(c["dimension"], FALLBACK_COLOR))
        inst[(kid, loc)] = {
            "value": val, "status": st,
            "color": lighten(base, level(kid) * LEVEL_LIGHTEN),
        }


# ---------------------------------------------------------------------------
# 5) GEXF schreiben
# ---------------------------------------------------------------------------
NODE_ATTRS = [("0", "node_type", "string"), ("1", "dimension", "string"),
              ("2", "subdimension", "string"), ("3", "level", "integer"),
              ("4", "unit", "string"), ("5", "value", "double"),
              ("6", "target", "double"), ("7", "status", "string"),
              ("8", "location_id", "string"), ("9", "factory", "string"),
              ("10", "measures_behind", "integer")]
EDGE_ATTRS = [("0", "strength", "double")]

out = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<gexf xmlns="http://www.gexf.net/1.3" version="1.3" '
       'xmlns:viz="http://www.gexf.net/1.3/viz" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
       'xsi:schemaLocation="http://www.gexf.net/1.3 http://www.gexf.net/1.3/gexf.xsd">',
       '  <meta><creator>KPI Node-Link (minimal)</creator>'
       '<description>KPI-Vorlagen x Fabrik-Struktur -> Instanzen je Ort</description></meta>',
       '  <graph defaultedgetype="directed" mode="static">',
       '    <attributes class="node" mode="static">']
out += [f'      <attribute id="{i}" title="{t}" type="{ty}"/>' for i, t, ty in NODE_ATTRS]
out += ['    </attributes>', '    <attributes class="edge" mode="static">']
out += [f'      <attribute id="{i}" title="{t}" type="{ty}"/>' for i, t, ty in EDGE_ATTRS]
out += ['    </attributes>', '    <nodes>']

for (kid, loc), d in inst.items():
    c = catalogue[kid]
    r, g, b = d["color"]
    x, y = pos[(kid, loc)]
    nid = f"{kid}@{loc}"
    out.append(f'      <node id="{escape(nid)}" label="{escape(c["label"])}">')
    out.append('        <attvalues>')
    vals = [("0", c["node_type"]), ("1", c["dimension"]), ("2", c["subdimension"]),
            ("3", level(kid)), ("4", c["unit"]), ("5", round(d["value"], 4)),
            ("6", c["target"]), ("7", d["status"]), ("8", loc),
            ("9", factory_of(loc)), ("10", mcount[kid])]
    for aid, v in vals:
        if v not in ("", None):
            out.append(f'          <attvalue for="{aid}" value="{escape(str(v))}"/>')
    out.append('        </attvalues>')
    out.append(f'        <viz:size value="{size[kid]}"/>')
    out.append(f'        <viz:position x="{x}" y="{y}" z="0.0"/>')
    out.append(f'        <viz:color r="{r}" g="{g}" b="{b}"/>')
    out.append('      </node>')

out.append('    </nodes>')
out.append('    <edges>')
eid = 0
for loc in locations:
    for kid, c in catalogue.items():
        if not c["formula"]:
            continue
        tgt_val = inst[(kid, loc)]["value"]
        for ref in REF.findall(c["formula"]):
            src_val = inst[(ref, loc)]["value"]
            strength = round(min(abs(src_val) / abs(tgt_val), 1.0), 4) if tgt_val else 0.0
            r, g, b = inst[(ref, loc)]["color"]     # Kante erbt Farbe der Quelle
            out.append(f'      <edge id="{eid}" source="{escape(ref + "@" + loc)}" '
                       f'target="{escape(kid + "@" + loc)}" weight="{strength}">')
            out.append(f'        <attvalues><attvalue for="0" value="{strength}"/></attvalues>')
            out.append(f'        <viz:color r="{r}" g="{g}" b="{b}"/>')
            out.append(f'        <viz:thickness value="{1 + strength * 7:.2f}"/>')
            out.append('      </edge>')
            eid += 1
out += ['    </edges>', '  </graph>', '</gexf>']

(OUT / "kpi_model.gexf").write_text("\n".join(out) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# 6) Log-Datei
#    Enthält je KPI und Ort den berechneten Wert und ob der Zielwert
#    erreicht wurde ([reached] / [missed] / [no target]).
# ---------------------------------------------------------------------------
from datetime import datetime

log = [f"Lauf: {datetime.now():%Y-%m-%d %H:%M:%S}",
       f"{len(inst)} Knoten, {eid} Kanten  ->  {OUT / 'kpi_model.gexf'}",
       ""]
for loc in locations:
    log.append(f"{loc}  ({factory_of(loc)})")
    for kid, c in catalogue.items():
        if c["node_type"] != "Measure":
            d = inst[(kid, loc)]
            log.append(f"  {c['label']:26} = {d['value']:>12,.2f} "
                       f"{c['unit']:<10} [{d['status']}]")
    log.append("")

(OUT / "log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
print(f"Fertig. Details siehe {OUT / 'log.txt'}")
