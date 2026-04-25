# MCP Integration Boundary

Dieses Dokument definiert MCP in Ananta als **Hub-kontrollierte Integration**.

## Leitplanken

1. Hub bleibt zentrale Control Plane fuer Orchestrierung, Policy und Audit.
2. MCP ist kein Worker-seitiger Tool-Freiflug und kein Bypass fuer Governance.
3. Zugriff startet mit **read-only / default-deny**.

## Access-Modell

- Tool-Descriptor enthalten explizit:
  - `capability`
  - `risk_class`
  - `access_class` (`read` / `write` / `admin`)
  - `allowed_scopes`
- Unbekannte Tools sind standardmaessig nicht verfuegbar.
- Write/Admin Tools duerfen nicht als `default_enabled=true` ausgerollt werden.

## Approval- und Audit-Regeln

- Read-only MCP kann mit expliziter Capability+Policy-Gate genutzt werden.
- Write/Admin MCP-Operationen brauchen explizite Freigabe- und Policy-Pfade.
- MCP-Aufrufe muessen trace-/provenance-faehige Artefakte liefern.
- Audit muss mindestens erfassen:
  - aufrufendes Principal/Auth-Quelle
  - Tool-ID und Scope
  - Policy-Entscheidung (allow/deny + reason)
  - Ergebnisstatus

## Explizit ausgeschlossen

- Keine direkte Worker-zu-Worker-Orchestrierung via MCP.
- Keine Umgehung von Approval-, MutationGate- oder Artifact-Flows.
- Kein stilles Eskalieren von read-only zu write/admin.

## Einfuehrungsreihenfolge

1. Descriptor Registry + Schema (default-deny)
2. Read-only Adapter mit Capability/Policy Gate und Provenance
3. Erst danach optionale write/admin Pfade mit expliziten Freigaben

