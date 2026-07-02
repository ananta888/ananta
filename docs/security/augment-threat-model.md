# Threat Model: Augment/Auggie Integration

> AUG-900 | Security | Status: open

## Kontext

Dieses Dokument beschreibt systematisch die Risiken der optionalen
Augment/Auggie-Integration in Ananta. Es richtet sich an Entwickler, die die
Integration implementieren, und an Nutzer, die sie aktivieren wollen.

Augment/Auggie sind per Default deaktiviert. Dieses Dokument gilt für den Fall
einer expliziten Aktivierung.

---

## Zentrale Controls

Alle Gegenmaßnahmen bauen auf zwei Kern-Controls auf, die immer gelten müssen:

1. **Default-Deny**: Ohne explizite Erlaubnis ist kein Zugriff erlaubt.
   Fehlende Konfiguration = Blockade, nicht Erlaubnis.

2. **Task-Workspace-Copy**: Auggie läuft bei schreibenden Operationen
   ausschließlich auf einer isolierten Arbeitskopie, niemals direkt auf dem
   echten Repository.

Alle weiteren Maßnahmen setzen voraus, dass diese zwei Controls intakt sind.

---

## Bedrohungsmatrix

| # | Bedrohung | Angriffsvektor | Gegenmaßnahme | Test/Check |
|---|---|---|---|---|
| T1 | Sensible Dateien an externen Provider | AugmentContextProvider sendet `.env`, `.git`, Credentials | `allowed_paths` + `denied_paths`-Prüfung vor jedem Provider-Call; Default-Deny bei fehlendem Scope | AUG-101: `.env`, `.git`, `secrets/`, `node_modules` blockiert |
| T2 | Auggie führt unerwünschte Commands aus | `auggie --print --quiet` hat breite Rechte im Workspace | Task-Workspace-Copy; kein direkter Repo-Mount; Prompt-Envelope mit expliziten Verboten | AUG-303: Direkte Repo-Mutation blockiert |
| T3 | Prompt Injection im Repository | Schadcode in Repo-Dateien beeinflusst Auggie-Verhalten | Prompt-Envelope mit strukturierten Verboten; keine Shell-Interpolation von Dateiinhalten; Auggie-Output wird geparst, nicht ausgeführt | AUG-306: Command-Injection-Versuche im Prompt |
| T4 | Task-Copy enthält Secrets oder Symlink-Escapes | Copy-Logik folgt Symlinks außerhalb des erlaubten Scopes | Symlink-Escape-Check vor Copy-In; `denied_paths` werden nicht kopiert; explizite Pfad-Validierung | AUG-303: Symlink-Escape und Secret-Dateien in Copy |
| T5 | Diff manipuliert Policy-Dateien oder CI/CD | Auggie ändert `.github/workflows`, `ananta.yaml`, Policy-Dateien | Diff-Policy-Check: Policy-, CI/CD- und Config-Dateien erhalten hohen Risiko-Score; separates Approval-Gate | AUG-304: Diff auf `workflows/`, Policy-Dateien blockiert oder hoch markiert |
| T6 | Interactive Session bleibt hängen | PTY-Prozess läuft ohne Timeout, konsumiert Ressourcen | Idle-Timeout (default: 120s) + Gesamt-Timeout (default: 1800s) + Prozessbaum-Kill | AUG-501: Timeout-Test mit hängendem Fake-Prozess |
| T7 | Account-/Plan-Änderung verändert Auggie-Funktionen | Auggie-Capabilities ändern sich ohne Vorwarnung durch Anbieter | Healthcheck vor jeder Nutzung; Capabilities gecacht und mit vorherigem Stand verglichen; Änderung → Warnung + optionale Deaktivierung | AUG-003: Healthcheck erkennt fehlenden `--print --quiet`-Support |

---

## Remote vs. Lokal: Risikoprofil

| Eigenschaft | AugmentContextProvider (MCP) | AuggieCliWorker (lokal) |
|---|---|---|
| Daten verlassen die Maschine | Ja, bei MCP-Remote-Setup | Nein (Subprocess lokal) |
| Netzwerkabhängigkeit | Ja (MCP-Server) | Nein |
| Risiko: Datenabfluss | Hoch bei falscher Konfiguration | Niedrig |
| Risiko: Code-Ausführung | Keines (nur Retrieval) | Mittel (subprocess) |
| Primäre Gegenmaßnahme | `allowed_paths`-Filter vor Provider-Call | Task-Workspace-Copy + Prompt-Envelope |

Wenn Augment in einem Remote-Setup betrieben wird (MCP-Server auf externem Host),
gelten zusätzlich TLS-Validierung und Authentifizierung als Mindestanforderung.
Dieses Dokument geht von lokalem MCP-Server-Betrieb als Default aus.

---

## Offene Restrisiken

Diese Risiken sind bekannt und können nicht vollständig durch Ananta-interne
Maßnahmen beseitigt werden:

### R1 — MCP-Server-Schwachstellen in Augment selbst

Sicherheitslücken im Augment MCP-Server oder der `auggie` Binary liegen außerhalb
von Anantas Kontrolle. Gegenmaßnahme: Versionspinning, Healthcheck, schnelle
Deaktivierung bei bekannten CVEs.

### R2 — Plan-/Account-Limits ändern Auggie-Verhalten

Wenn der Augment-Plan sich ändert (z.B. `--print --quiet` wird kostenpflichtig
oder deaktiviert), kann Auggie sich anders verhalten als erwartet. Gegenmaßnahme:
Healthcheck vor jeder Nutzung, Capabilities-Caching mit Vergleich.

### R3 — LLM-Prompt-Injection durch böswillige Repositories

Ein Repository kann Dateien mit manipulierten Inhalten enthalten, die das
Auggie-LLM zu unerwünschten Aktionen verleiten sollen. Ananta kann den Auggie-
internen LLM-Aufruf nicht vollständig kontrollieren. Gegenmaßnahme:
Prompt-Envelope mit expliziten Verboten, Output-Parsing statt -Ausführung,
Auggie nur in Task-Copy. Restrisiko bleibt bei komplexen Injection-Versuchen.

---

## Audit-Anforderungen

Alle Augment/Auggie-Interaktionen müssen im AuditTrail erscheinen:

```json
{
  "event_type": "augment_provider_call",
  "run_id": "<run_id>",
  "provider": "augment_mcp",
  "query_hash": "sha256:...",
  "scope_paths_count": 3,
  "result_count": 8,
  "redacted_results": 2,
  "policy_decision": "allowed",
  "duration_ms": 1240
}
```

Blockierte Augment-Calls erhalten `policy_decision: blocked` mit `reason_code`.

---

## Deployment-Voraussetzungen vor Aktivierung

Checkliste, die vor produktiver Aktivierung von Augment/Auggie geprüft werden muss:

- [ ] Datenschutzprüfung für das Ziel-Repository abgeschlossen
- [ ] `denied_paths` für sensitive Dateien konfiguriert (`.env`, Credentials, Keys)
- [ ] Task-Workspace-Manager für das Deployment verfügbar und getestet
- [ ] Healthcheck-Endpoint erreichbar und liefert erwartete Capabilities
- [ ] Audit-Events werden geschrieben und sind abrufbar
- [ ] Rollback-Plan: Wie wird Augment deaktiviert wenn nötig?

---

## Weiterführend

- `docs/architecture/augment-auggie-integration.md` — Integrationsarchitektur
- `docs/architecture/transparency-and-local-safety.md` — Default-Deny-Prinzip
- `docs/architecture/local-only-mode.md` — Modus ohne externe Provider
