# Installation

## Voraussetzungen

- FreeCAD mit Python-Workbench-Support
- Zugriff auf einen Ananta-Hub-Endpunkt
- Token oder anderes Hub-Auth-Material ausserhalb des Logs

## Workbench-Struktur

Die Runtime liegt unter `client_surfaces/freecad/workbench/`.
Der aktuelle Stand ist ein testbares Runtime-Scaffold, kein voll integriertes Produktionspaket fuer den FreeCAD-Addon-Manager.

## Paket bauen

Das installierbare ZIP wird lokal erzeugt mit:

```bash
python scripts/build_freecad_workbench_package.py --out ci-artifacts/domain-runtime/freecad-workbench-addon.zip
```

Das Paket enthaelt `package.xml` sowie den benoetigten `client_surfaces/freecad/...`-Baum.

## Konfiguration

`FreecadWorkbenchSettings` erwartet:
- `endpoint`
- `profile`
- `token`
- `transport_mode` (`stub` oder `http`)
- `request_timeout_seconds`
- `max_context_objects`
- `max_payload_bytes`

Unsicheres `http://` ist nur mit explizitem Opt-in erlaubt.
