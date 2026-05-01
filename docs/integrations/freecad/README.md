# FreeCAD Runtime Surface

Der FreeCAD-Track liefert einen duennen Workbench-Client unter `client_surfaces/freecad/`.
Die Orchestrierung bleibt im Hub. Der Client erfasst bounded Kontextdaten, zeigt Approval-/Denial-Zustaende an und leitet Read/Plan/Execution-Requests an Hub-APIs weiter.

## Abgrenzung

Bereits vorher vorhanden war nur die Foundation:
- `schemas/freecad/*`
- `policies/freecad_policy.v1.json`
- `agent/services/freecad_*`
- `reference_sources/freecad/*`

Neu in diesem Track ist die echte Runtime-Struktur:
- `client_surfaces/freecad/workbench/*`
- `client_surfaces/freecad/bridge/*`
- `client_surfaces/freecad/package/*`
- `client_surfaces/freecad/tests/*`
- `scripts/run_freecad_smoke_checks.py`
- `scripts/build_freecad_workbench_package.py`
