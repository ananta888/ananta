# Installation

## Voraussetzungen

- FreeCAD mit Python-Workbench-Support
- Zugriff auf einen Ananta-Hub-Endpunkt
- Token oder anderes Hub-Auth-Material ausserhalb des Logs

## Workbench-Struktur

Die Runtime liegt unter `client_surfaces/freecad/workbench/`.
Der aktuelle Stand ist ein testbares Runtime-Scaffold, kein voll integriertes Produktionspaket fuer den FreeCAD-Addon-Manager.

## Konfiguration

`FreecadWorkbenchSettings` erwartet:
- `endpoint`
- `profile`
- `token`
- `request_timeout_seconds`
- `max_context_objects`
- `max_payload_bytes`

Unsicheres `http://` ist nur mit explizitem Opt-in erlaubt.
