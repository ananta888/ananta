# Usage

## Bounded Context

Der Client erfasst nur begrenzte Dokument-, Objekt-, Selektions- und Constraint-Daten. Lokale Dateipfade werden redigiert. Oversize-Payloads werden gekuerzt und im Preview markiert.
Neben dict-Payloads kann der Adapter jetzt auch direkt gegen FreeCAD-Runtime-Objekte arbeiten:

- `capture_context_from_freecad_document(...)`
- `capture_active_freecad_context(...)`

## Goal Submit

`submit_freecad_goal(...)` sendet Zieltext plus bounded Kontext an den Hub. Im Client wird kein lokaler Plan erzeugt.

## HTTP-Transport

Fuer echte Hub-Anbindung kann der Client mit `transport_mode=\"http\"` oder `FreecadHubClient.with_http_transport(...)` betrieben werden. Die HTTP-Routen bleiben duenn und JSON-basiert; Fehler werden als strukturierte degraded/unauthorized/approval-required Zustaende zurueckgegeben.

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
