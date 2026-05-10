# ADR: Wiki ZIM Parser Strategy

## Status
Accepted (2026-05-10)

## Decision
ZIM bleibt im Produktpfad vorerst **fail-closed**. Primärer Produktionspfad ist weiterhin Wikimedia XML/XML.BZ2 (inkl. Multistream).

## Optionen
1. `libzim` native binding (hohe Performance, aber Packaging/ABI-Risiko auf Android).
2. `pyzim`/pure Python (einfacher, aber unklare Performance für große Dumps).
3. Externe Kiwix-Toolchain als Vorverarbeitung (klarer Runtime-Schnitt, zusätzlicher Build-/Ops-Aufwand).
4. Serverseitige Vorverarbeitung in dediziertem Worker-Container (gute Entkopplung, kein On-Device-Zwang).

## Begründung
- Android- und Container-Reproduzierbarkeit haben Priorität.
- Der XML-Pfad ist bereits testbar und betrieblich beherrschbar.
- ZIM wird erst aktiviert, wenn ABI, Lizenz und Performance im Ziel-Setup belastbar validiert sind.

## Konsequenzen
- `.zim` Quellen bleiben sichtbar, aber nicht importierbar.
- API liefert klare `unsupported`-Fehler statt stiller Fallbacks.
- Optionaler ZIM-Prototyp (`WR2-T35`) bleibt bis zur expliziten Freigabe blockiert.
