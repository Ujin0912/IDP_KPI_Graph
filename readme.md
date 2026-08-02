KPI Node-Link Graph

build_kpi_graph.py verwandelt drei einfache CSV-Dateien in einen gerichteten 
Node-Link-Graphen (.gexf, z.B. für Gephi). Ein KPI wird dabei nur einmal 
als Vorlage definiert (Formel, Dimension, Zielwert). Jeder Ort (Fabrik / Linie / 
Zelle), für den Messwerte vorliegen, bekommt automatisch eine eigene Instanz 
des kompletten KPI-Baums. So teilen sich mehrere Fabriken dieselben KPIs, ohne 
dass eine Formel dupliziert werden muss.

Im Graphen gilt:
- Knotenfarbe = Dimension (Environmental / Economic / Social), pro Ebene heller
- Knotengröße = Anzahl der Messgrößen, die in den Knoten einfliessen
- Pfeile = Einflussrichtung (Messgröße -> KPI), Dicke = Einflussstärke

Benötigte Dateien (im Ordner Resources/)

Alle drei Dateien sind CSV mit Semikolon (;) als Trennzeichen und müssen 
im Ordner Resources/ liegen.

1. kpi_catalogue.csv — die KPI-Vorlagen

Eine Zeile pro KPI, Sub-KPI oder Messgröße. \
Spalten:

- kpi_id — eindeutige ID (z.B. ENV-01)
- label — Anzeigename
- node_type — KPI, Sub-KPI oder Measure
- dimension — eine von: Environmental, Economic, Social
- subdimension — freier Text zur Gruppierung (z.B. Cost, Energy, Workforce)
- parent — kpi_id des übergeordneten Knotens (leer bei der Wurzel)
- unit — Einheit (z.B. EUR/cell)
- formula — Berechnung über andere IDs in {...}, z.B. {ENV-11}/{PROD-01} 
- (leer bei einer Messgröße)
- target — Zielwert (leer, wenn kein Ziel)
- target_direction — min (kleiner ist besser) oder max (größer ist besser)

Beispiel:

```
kpi_id;label;node_type;dimension;subdimension;parent;unit;formula;target;target_direction
ENV-01;Energy per cell;KPI;Environmental;Energy;;kWh/cell;{ENV-11}/{PROD-01};15;min
ENV-11;Electricity consumption;Measure;Environmental;Energy;ENV-01;kWh;;;
SOC-01;Training hours per worker;KPI;Social;Workforce;;h/worker;{SOC-11}/{SOC-12};20;max
COST-01;Cost per cell;KPI;Economic;Cost;;EUR/cell;{COST-02}/{PROD-01};5;min'
```

2. structure.csv — die Fabrik-Struktur

Eine Zeile pro Struktur-Knoten. \
Spalten:

- node_id — eindeutige ID (z.B. F1-L1-C1)
- label — Anzeigename
- level — Factory, Line oder Cell
- parent — node_id des übergeordneten Knotens (leer bei einer Fabrik)

Beispiel:

```
node_id;label;level;parent\
F1;Factory 1;Factory;\
F1-L1;Electrode Line;Line;F1\
F1-L1-C1;Formation Cell;Cell;F1-L1\
F2;Factory 2;Factory;
```

3. measure_data.csv — die Messwerte

Eine Zeile pro Messwert: welcher Messgröße-Wert an welchem Ort gemessen wurde. 
Nur Measure-IDs aus dem Katalog werden hier befüllt. \
Spalten:

- kpi_id — ID einer Messgröße aus kpi_catalogue.csv
- location_id — ID eines Ortes aus structure.csv
- value — Zahl

Beispiel:

```
kpi_id;location_id;value\
ENV-11;F1-L1-C1;900000\
PROD-01;F1-L1-C1;50000\
SOC-11;F1-L1-C1;900\
ENV-11;F2-L1-C1;870000
```


Ausführen und Ergebnisse

Skript starten (die Pfade sind relativ zum Skript, funktioniert also aus 
PyCharm oder aus dem Terminal):

python build_kpi_graph.py


Die Ergebnisse liegen danach im Ordner Output/:

- kpi_model.gexf — der Graph zum Öffnen in Gephi
- log.txt — Protokoll des Laufs. Hier steht je KPI und Ort der berechnete 
- Wert und ob der Zielwert erreicht wurde ([reached] / [missed] / 
- [no target]).

Ordnerstruktur

```
pythonProject/
├── build_kpi_graph.py
├── README.md
├── Resources/
│   ├── kpi_catalogue.csv
│   ├── structure.csv
│   └── measure_data.csv
└── Output/            (wird automatisch erzeugt)
    ├── kpi_model.gexf
    └── log.txt
```
