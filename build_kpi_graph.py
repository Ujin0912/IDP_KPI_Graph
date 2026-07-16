"""
build_kpi_graph.py
------------------
INPUT   (erwartet in  <script dir>/Resources/ )
  kpi_structure.csv  : KPI KAtalog (KPIs, Sub-KPIs, Messgrößen) inkl. Formeln
  measure_data.csv   : NUR reine Messgrößendaten (was der Benutzer liefern soll)

OUTPUT  (in <script dir>/Output/ )
  kpi_values.csv     : KPIs + Sub-KPIs mit Formeln, ausgerechneten Werten und "Zielwerten"
  node_table.csv     : Tabelle mit allen Nodes (Messgrößen + KPIs) -- inspection / debug
  kpi_model.gexf     : .gexf Node-Link Graph für Gephi

Formeln sind im kpi_structure file ausgedrückt mit anderen node-ids,
z.B.{ECO-02} + {ECO-03}   or later  {ECO-01} / {ECO-02}
"""

import csv
import re
import colorsys
from pathlib import Path
from xml.sax.saxutils import escape


BASE_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = BASE_DIR / "Resources"
OUTPUT_DIR = BASE_DIR / "Output"
OUTPUT_DIR.mkdir(exist_ok=True)

STRUCTURE_CSV = RESOURCES_DIR / "kpi_structure.csv"
DATA_CSV = RESOURCES_DIR / "measure_data.csv"

for _p in (STRUCTURE_CSV, DATA_CSV):
    if not _p.exists():
        raise FileNotFoundError(
            f"Input file not found: {_p}\n(current working directory is: {Path.cwd()})")

# ---------------------------------------------------------------------------
# CONFIG -- zusätzliche Dimensionen?
# ---------------------------------------------------------------------------
DIMENSIONS = {
    "Environmental": "#0072B2",   # blau
    "Economic":      "#E69F00",   # orange
    "Cat3":          "#009E73",   # grün
    "Cat4":          "#CC79A7",   # magenta
}
FALLBACK_COLOR = "#999999"

# hellere Farben für niedrigere Dimensionen
SUBDIM_LIGHTEN = 0.12
LEVEL_LIGHTEN = 0.30


# --- Knotengrösse ---------------------------------------------------------
# Groesse skaliert mit der Anzahl Measures, die in einen Knoten einfliessen.
# Diese Anzahl wird linear auf [MAX_NODE_SIZE * MIN_SIZE_RATIO, MAX_NODE_SIZE]
# abgebildet. MIN_SIZE_RATIO stellt sicher, dass der kleinste Knoten nicht zu
# klein wird und Measures sichtbar bleiben.
MAX_NODE_SIZE = 60.0         # groesster Knoten (meiste Measures dahinter)
MIN_SIZE_RATIO = 0.25        # kleinster Knoten = 25% des groessten


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lighten(rgb, t):
    """Farbe mit weiß vermischen t (0..1)"""
    return tuple(int(round(c + (255 - c) * t)) for c in rgb)


def shift_hue(rgb, deg):
    r, g, b = [c / 255 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + deg / 360.0) % 1.0, l, s)
    return tuple(int(round(c * 255)) for c in (r, g, b))


def read_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ---------------------------------------------------------------------------
# 1) Struktur- und Messwerte laden
# ---------------------------------------------------------------------------
nodes = {n["id"]: n for n in read_csv(STRUCTURE_CSV)}
order = list(nodes)
data = {d["id"]: float(d["value"]) for d in read_csv(DATA_CSV)}

for nid, n in nodes.items():
    if n["node_type"] == "Measure":
        if nid not in data:
            raise ValueError(f"No measure value delivered for {nid} ({n['label']})")
        n["value"] = data[nid]
    else:
        n["value"] = None

# ---------------------------------------------------------------------------
# 2) Formeln ausführen von Sub-KPIs
# ---------------------------------------------------------------------------
REF = re.compile(r"\{([^}]+)\}")


def resolve(nid, stack=()):
    n = nodes[nid]
    if n["value"] is not None:
        return n["value"]
    if nid in stack:
        raise ValueError(f"Circular formula: {' -> '.join(stack + (nid,))}")
    formula = n["formula"]
    if not formula:
        raise ValueError(f"{nid} has neither a value nor a formula")
    expr = REF.sub(lambda m: repr(resolve(m.group(1), stack + (nid,))), formula)
    n["value"] = float(eval(expr, {"__builtins__": {}}, {}))
    return n["value"]


for nid in order:
    resolve(nid)

# --- Kanten: "Quelle beeinflusst Ziel", aus den Formeln abgeleitet ---------
edges = []
for nid, n in nodes.items():
    for ref in REF.findall(n["formula"] or ""):
        edges.append({"source": ref, "target": nid})

# Einflussstaerke = Beitragsanteil der Quelle am Zielwert
for e in edges:
    tgt = nodes[e["target"]]["value"]
    src = nodes[e["source"]]["value"]
    share = abs(src) / abs(tgt) if tgt else 0.0
    e["strength"] = round(min(share, 1.0), 4)
    e["influence"] = "positive"          # vorerst nur Summen
    e["relation"] = "aggregation"

indeg = {nid: 0 for nid in nodes}
for e in edges:
    indeg[e["target"]] += 1

children = {nid: [] for nid in nodes}
for nid, n in nodes.items():
    if n["parent"]:
        children[n["parent"]].append(nid)


def measure_count(nid):
    # wie viele Measure-Knoten letztlich in diesen Knoten einfliessen
    if nodes[nid]["node_type"] == "Measure":
        return 0
    return sum(1 if nodes[c]["node_type"] == "Measure" else measure_count(c)
               for c in children[nid])


# ---------------------------------------------------------------------------
# 3) Zielstatus
# ---------------------------------------------------------------------------
def status(n):
    if not n["target"]:
        return "no target"
    ok = (n["value"] <= float(n["target"])) if n["target_direction"] == "min" \
        else (n["value"] >= float(n["target"]))
    return "reached" if ok else "missed"


for n in nodes.values():
    n["status"] = status(n)

# ---------------------------------------------------------------------------
# 4) Farben (Dimension -> Subdimension -> Ebene) und Groessen
# ---------------------------------------------------------------------------
subdims = {}
for n in nodes.values():
    subdims.setdefault(n["dimension"], [])
    if n["subdimension"] not in subdims[n["dimension"]]:
        subdims[n["dimension"]].append(n["subdimension"])


def level(nid):
    lvl, cur = 0, nodes[nid]
    while cur["parent"]:
        lvl += 1
        cur = nodes[cur["parent"]]
    return lvl


for nid, n in nodes.items():
    base = hex_to_rgb(DIMENSIONS.get(n["dimension"], FALLBACK_COLOR))
    i = subdims[n["dimension"]].index(n["subdimension"])
    rgb = shift_hue(base, i * 12)                      # Subdimension: ähnlicher Farbton
    rgb = lighten(rgb, i * SUBDIM_LIGHTEN + level(nid) * LEVEL_LIGHTEN)
    n["color"] = rgb
    n["level"] = level(nid)
    n["measures_behind"] = measure_count(nid)

# "measures_behind" auf [min_size, MAX_NODE_SIZE] abbilden, damit der kleinste
# Knoten nie kleiner als MIN_SIZE_RATIO * MAX_NODE_SIZE wird
min_size = MAX_NODE_SIZE * MIN_SIZE_RATIO
counts = [n["measures_behind"] for n in nodes.values()]
lo, hi = min(counts), max(counts)
span = (hi - lo) or 1                                  # Division durch 0 vermeiden
for n in nodes.values():
    t = (n["measures_behind"] - lo) / span             # 0..1
    n["size"] = min_size + t * (MAX_NODE_SIZE - min_size)

# ---------------------------------------------------------------------------
# 5) Layout: ein Baum pro Wurzel, Ebene = x, Geschwister ueber y verteilt
# ---------------------------------------------------------------------------
roots = [nid for nid, n in nodes.items() if not n["parent"]]
pos, y_cursor = {}, [0.0]


def place(nid, depth):
    kids = children[nid]
    if not kids:
        y = y_cursor[0]
        y_cursor[0] += 110.0
    else:
        ys = [place(k, depth + 1) for k in kids]
        y = sum(ys) / len(ys)
    pos[nid] = (depth * -260.0, y)     # Wurzel rechts, Measures links
    return y


for r in roots:
    place(r, 0)
    y_cursor[0] += 140.0                # Abstand zwischen den beiden Baeumen

# ---------------------------------------------------------------------------
# 6) CSV-Ausgaben
# ---------------------------------------------------------------------------
with open(OUTPUT_DIR / "kpi_values.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["id", "label", "node_type", "dimension", "subdimension",
                "formula", "value", "unit", "target", "status"])
    for nid in order:
        n = nodes[nid]
        if n["node_type"] == "Measure":
            continue
        w.writerow([nid, n["label"], n["node_type"], n["dimension"], n["subdimension"],
                    n["formula"], round(n["value"], 4), n["unit"], n["target"], n["status"]])

with open(OUTPUT_DIR / "node_table.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["id", "label", "node_type", "dimension", "subdimension", "level",
                "parent", "value", "unit", "target", "status",
                "in_degree", "measures_behind", "size", "color"])
    for nid in order:
        n = nodes[nid]
        w.writerow([nid, n["label"], n["node_type"], n["dimension"], n["subdimension"],
                    n["level"], n["parent"], round(n["value"], 4), n["unit"],
                    n["target"], n["status"], indeg[nid], n["measures_behind"],
                    n["size"], "#%02X%02X%02X" % n["color"]])

# ---------------------------------------------------------------------------
# 7) GEXF Ausgabe
# ---------------------------------------------------------------------------
NODE_ATTRS = [("0", "node_type", "string"), ("1", "dimension", "string"),
              ("2", "subdimension", "string"), ("3", "level", "integer"),
              ("4", "unit", "string"), ("5", "value", "double"),
              ("6", "target", "double"), ("7", "status", "string"),
              ("8", "formula", "string"), ("9", "in_degree", "integer"),
              ("10", "measures_behind", "integer")]
EDGE_ATTRS = [("0", "influence", "string"), ("1", "strength", "double"),
              ("2", "relation", "string"), ("3", "sign", "integer")]

out = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<gexf xmlns="http://www.gexf.net/1.3" version="1.3" '
       'xmlns:viz="http://www.gexf.net/1.3/viz" '
       'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
       'xsi:schemaLocation="http://www.gexf.net/1.3 http://www.gexf.net/1.3/gexf.xsd">',
       '  <meta><creator>KPI Node-Link Prototype</creator>'
       '<description>Two KPI trees (Costs / Emissions to air), depth 3</description></meta>',
       '  <graph defaultedgetype="directed" mode="static">',
       '    <attributes class="node" mode="static">']
out += [f'      <attribute id="{i}" title="{t}" type="{ty}"/>' for i, t, ty in NODE_ATTRS]
out += ['    </attributes>', '    <attributes class="edge" mode="static">']
out += [f'      <attribute id="{i}" title="{t}" type="{ty}"/>' for i, t, ty in EDGE_ATTRS]
out += ['    </attributes>', '    <nodes>']

for nid in order:
    n = nodes[nid]
    r, g, b = n["color"]
    x, y = pos[nid]
    out.append(f'      <node id="{nid}" label="{escape(n["label"])}">')
    out.append('        <attvalues>')
    vals = [("0", n["node_type"]), ("1", n["dimension"]), ("2", n["subdimension"]),
            ("3", n["level"]), ("4", n["unit"]), ("5", round(n["value"], 4)),
            ("6", n["target"]), ("7", n["status"]), ("8", n["formula"]),
            ("9", indeg[nid]), ("10", n["measures_behind"])]
    for aid, v in vals:
        if v not in ("", None):
            out.append(f'          <attvalue for="{aid}" value="{escape(str(v))}"/>')
    out.append('        </attvalues>')
    out.append(f'        <viz:size value="{n["size"]}"/>')
    out.append(f'        <viz:position x="{x}" y="{y}" z="0.0"/>')
    out.append(f'        <viz:color r="{r}" g="{g}" b="{b}"/>')
    out.append('      </node>')

out.append('    </nodes>')
out.append('    <edges>')
for i, e in enumerate(edges):
    r, g, b = nodes[e["source"]]["color"]       # Kante erbt die Farbe der Quelle
    sign = 1 if e["influence"] == "positive" else -1
    out.append(f'      <edge id="{i}" source="{e["source"]}" target="{e["target"]}" '
               f'weight="{e["strength"]}">')
    out.append('        <attvalues>')
    out.append(f'          <attvalue for="0" value="{e["influence"]}"/>')
    out.append(f'          <attvalue for="1" value="{e["strength"]}"/>')
    out.append(f'          <attvalue for="2" value="{e["relation"]}"/>')
    out.append(f'          <attvalue for="3" value="{sign}"/>')
    out.append('        </attvalues>')
    out.append(f'        <viz:color r="{r}" g="{g}" b="{b}"/>')
    out.append(f'        <viz:thickness value="{1 + e["strength"] * 7:.2f}"/>')
    out.append('      </edge>')
out += ['    </edges>', '  </graph>', '</gexf>']

with open(OUTPUT_DIR / "kpi_model.gexf", "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

print(f"OK: {len(nodes)} nodes, {len(edges)} edges  ->  {OUTPUT_DIR}")
for nid in order:
    n = nodes[nid]
    if n["node_type"] != "Measure":
        print(f"  {nid:8} {n['label']:42} = {n['value']:>10,.0f} {n['unit']:<12} "
              f"[{n['status']:<9}] size={n['size']:.0f}")