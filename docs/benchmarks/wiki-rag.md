# Wiki-RAG Benchmark

## Ziel
Mittelgroße Dump-Ausschnitte reproduzierbar messen (Import + Index + Retrieval).

## Metriken
- Seiten/s
- Chunks/s
- Indexgröße (MB)
- Grobe Laufzeit je Phase (Download/Parse/Index)

## Scope
- Lokal ausführbar, nicht Teil der schnellen Standard-CI.
- Ergebnis dient als Kapazitätsabschätzung für Android (klein) vs. Desktop/Server (voll).

## Hinweis
Die Messung basiert auf Fixture-/Ausschnittdaten und ist **kein** Vollnachweis für den kompletten `dewiki` Dump.
