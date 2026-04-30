# Usage

## Bounded Context

Der Client erfasst nur begrenzte Dokument-, Objekt-, Selektions- und Constraint-Daten. Lokale Dateipfade werden redigiert. Oversize-Payloads werden gekuerzt und im Preview markiert.

## Goal Submit

`submit_freecad_goal(...)` sendet Zieltext plus bounded Kontext an den Hub. Im Client wird kein lokaler Plan erzeugt.

## Approvals und Safety

- `freecad.document.read`, `freecad.model.inspect`, `freecad.export.plan`, `freecad.macro.plan` bleiben lesend bzw. planend.
- `freecad.macro.execute` bleibt approval-gated.
- Denied oder pending Aktionen fuehren nie zur lokalen Ausfuehrung.

## Smoke Evidence

Der Track bringt ein reproduzierbares Smoke-Skript mit:

```bash
python scripts/run_freecad_smoke_checks.py
```

Damit wird nur der Workbench-/Bridge-Grundvertrag geprueft, nicht die echte FreeCAD-GUI-Laufzeit.
