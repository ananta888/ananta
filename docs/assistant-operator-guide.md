# Assistant Operator Guide

## Ziel
Der Assistant ist als globales Chat-Dock unten rechts verfuegbar und kann projektbezogene Lese- und Schreibaktionen ausfuehren.

## Bedienung
- Dock oeffnen/schliessen ueber den Header.
- Auf Mobile oeffnet das Dock im Expanded-Status als Fullscreen-Overlay.
- Chat-History wird lokal gespeichert und nach Reload wiederhergestellt.

## Kontext
Jede Anfrage enthaelt Laufzeitkontext:
- aktuelle Route
- ausgewaehlter Agent (falls Panel-Route)
- Benutzername und Rolle
- Anzahl Agents/Teams/Templates
- kompakter, sensibel bereinigter Config-Snapshot

## Schreibaktionen und Confirm-Flow
- Tool-Aktionen werden als Plan angezeigt.
- Pro Aktion werden Scope, erwarteter Effekt und Change-Zusammenfassung angezeigt.
- Vor Ausfuehrung muss explizit `RUN` eingegeben werden.

## Sicherheit
- Serverseitiger Capability-Contract steuert erlaubte Tools.
- Mutierende Tools sind ohne Admin-Rechte blockiert.
- Tool-Allowlist/Denylist wird serverseitig erzwungen.
- Sensible Konfigurationswerte (z.B. `*_api_key`, `*token*`, `*secret*`, `*password*`) werden im Read-Model redacted.

## Betriebsrelevante Endpunkte
- `GET /assistant/read-model`: Aggregierter Read-Model Kontext fuer den Assistant.
- `POST /llm/generate`: Assistant-LLM inkl. `assistant_capabilities` Metadaten.

## Fehlerbilder
- `tool_not_allowed_by_capability_contract`: Tool nicht erlaubt.
- `admin_required_for_mutating_tool`: Schreibaktion ohne Admin.
- `unknown_tool`: Unbekanntes Tool im Plan.

## Empfohlene Tests
- Unit: Kontext-Building, History-Persistenz, Change-Summaries.
- E2E: globales Dock ueber mehrere Routen, Confirm-Flow mit `RUN`.
- Backend: Capability-Contract und `assistant_capabilities` Response-Metadaten.
