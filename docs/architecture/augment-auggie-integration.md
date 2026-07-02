# Augment/Auggie Integration

> AUG-000 | Architekturentscheidung | Status: open

## Entscheidung

Augment und Auggie werden als optionale externe Erweiterungen integriert.
Sie sind kein Ersatz für CodeCompass, keinen Hub-Bypass und keine neue
Orchestrierungsschicht. Sie laufen vollständig unter Ananta-Policy, Audit und
Task-Scope-Grenzen.

**Default: vollständig deaktiviert** (`augment.enabled: false`)

---

## Drei Integrationspfade

Jeder Pfad ist separat aktivierbar und deaktivierbar. Sie teilen gemeinsame
Config, Healthcheck, PolicyEngine, Redaction und AuditTrail.

---

### 1. AugmentContextProvider (MCP / codebase-retrieval)

**Zweck**: Augment Context Engine als optionalen Retrieval-Provider anbinden.

**Mechanismus**: Auggie MCP-Server liefert `codebase-retrieval`-Ergebnisse.
Ananta normalisiert diese auf CodeCompass-kompatible `ContextItem`-Objekte.

**Modul**: `agent/services/context_providers/augment_context_provider.py`

**Aktivierung**: `augment.mcp.enabled: true` + Healthcheck ok

**Verhalten**:
- Ergebnisse erhalten `provider: "augment"` und `source_kind: "external"`
- Im ContextTrace sind Augment-Treffer klar als externe Evidence markiert
- Routing durch CodeCompass (codecompass_only / hybrid_fallback / hybrid_parallel)
- Default-Routing: `codecompass_only` — Augment ist nicht automatisch aktiv

**Was dieser Pfad nicht tut**:
- Keine Policy-Entscheidungen
- Kein Zugriff auf Pfade außerhalb `allowed_paths`
- Keine Weitergabe von `denied_paths` an Augment

---

### 2. AuggieCliWorker (auggie --print --quiet)

**Zweck**: Nicht-interaktive Analyse-Aufgaben über Auggie CLI ausführen,
in einem kontrollierten Task-Workspace.

**Mechanismus**: `auggie --print --quiet <prompt>` in isolierter Umgebung.
Strukturierter JSON-Output wird geparst. Kein Shell-Interpolation.

**Modul**: `agent/services/worker_backends/auggie_cli_worker.py`

**Aktivierung**: `augment.auggie_cli.enabled: true` + Healthcheck ok

**Verhalten**:
- Worker läuft in Task-Workspace-Copy, nie direkt auf dem echten Repo
- `allow_write: false` ist der Default — Schreibmodus muss explizit aktiviert werden
- Prompt-Envelope definiert erlaubte Pfade, verbotene Ausgaben und erwartetes Format
- stdout/stderr werden längenbegrenzt gespeichert
- Exit-Code, Timeout und Diagnostik sind strukturiert im WorkerRunResult

**Was dieser Pfad nicht tut**:
- Kein direkter Repo-Mount
- Keine Shell-Interpolation im Command-Building
- Keine Secrets in der ENV-Übergabe (allowlist-basiert)
- Kein Hub-Bypass

---

### 3. AuggieInteractiveBridge (später)

**Zweck**: Kontrollierte Auggie-Sessions für komplexe interaktive Aufgaben.

**Status**: Konzept definiert, Implementierung nach Stabilisierung von Pfad 1+2.

**Mechanismus**: Session-Port mit `start_session`, `send_message`, `stop_session`,
`get_transcript`. PTY-Code liegt isoliert im Bridge-Modul.

**Modul**: `agent/services/interactive_bridges/auggie_interactive_bridge.py`

**Aktivierung**: `augment.interactive_bridge.enabled: true`

**Kern-Kontrollen**:
- Jede Session hat Scope, Owner, Correlation-ID und Timeout
- Idle-Timeout beendet hängende Sessions (default: 120s)
- Transcript wird redigiert und auditiert
- Dateienänderungen in der Session → DiffProposal, niemals direktes Apply
- Session darf keine Git-Push/PR-Aktionen auslösen ohne explizite Policy-Freigabe

---

## Was Augment/Auggie nicht trifft

| Verboten | Begründung |
|---|---|
| Policy-Entscheidungen | PolicyEngine liegt bei Ananta-Hub |
| Hub-Bypass | Auggie ist Worker/Provider, nicht Orchestrator |
| Direkte Dateiänderungen im echten Repo | Pflicht: Task-Workspace-Copy + Diff + Approval |
| Secrets ohne Whitelist | ENV wird allowlist-basiert übergeben |
| Netzwerkzugriff ohne Scope | Netzwerk-Policy gilt auch für Auggie-Subprozesse |

---

## Task-Workspace-Copy als Pflicht

Für alle schreibenden Auggie-Operationen ist eine isolierte Arbeitskopie
des Workspace zwingend:

1. Hub erstellt Task-Copy mit `TaskWorkspaceManager`
2. Copy respektiert `allowed_paths` und `denied_paths`
3. Symlink-Escape-Check vor Copy-In
4. Auggie läuft gegen die Copy, nicht das echte Repo
5. Nach Lauf: Diff gegen Baseline → `DiffProposal`
6. Apply nur nach `ApprovalRecord`

---

## Architektur-Mapping

```
AnantaHub
  ├── PolicyEngine          (erzwingt Default-Deny für alle Augment-Zugriffe)
  ├── TaskWorkspaceManager  (erstellt isolierte Copies)
  ├── AuditTrail            (speichert alle Augment-Interaktionen)
  │
  ├── CodeCompass           (primärer erklärbarer Kontext-Layer, bleibt unverändert)
  │     └── AugmentContextProvider  (optional, normalisiert auf ContextItems)
  │
  ├── AuggieCliWorker       (optional, read-only oder write-proposal in Task-Copy)
  └── AuggieInteractiveBridge (optional, später)
```

CodeCompass bleibt die primäre, erklärbare Kontext- und Graph-Schicht.
Augment-Ergebnisse sind im ContextTrace als `source_kind: external` sichtbar.

---

## Abhängigkeiten vor Implementierung

Diese Punkte müssen verifiziert werden, bevor Implementierungsarbeit beginnt:

- [ ] Node.js 20+ vorhanden in der Zielumgebung
- [ ] `auggie` CLI installiert und `auggie login` aktiv
- [ ] `auggie --print --quiet` im genutzten Account/Plan erlaubt
- [ ] Augment MCP: exakte Tool-Schema-Version und Startsequenz bekannt
- [ ] Datenschutz und Geheimhaltung für das jeweilige Repo geklärt
- [ ] `auggie --version` gibt stabile semver-Ausgabe zurück (für Healthcheck)

---

## Konfigurationsmodell (Auszug)

```yaml
augment:
  enabled: false
  mcp:
    enabled: false
    tool_name: codebase-retrieval
    timeout_seconds: 45
    max_results: 12
  auggie_cli:
    enabled: false
    command: auggie
    default_args: ["--print", "--quiet"]
    requires_login: true
    timeout_seconds: 300
    max_output_bytes: 1048576
    allow_write: false
  interactive_bridge:
    enabled: false
    max_session_seconds: 1800
    idle_timeout_seconds: 120
    approval_required_for_write: true
  security:
    workspace_mode: task_scoped_copy
    send_secrets: false
    redact_env: true
    denied_paths: [".git", ".env", ".venv", "node_modules", "secrets"]
```

---

## Weiterführend

- `docs/security/augment-threat-model.md` — Bedrohungsmodell und Gegenmaßnahmen
- `docs/architecture/transparency-and-local-safety.md` — Transparenz-Manifest
- `docs/cli-backends-architecture.md` — Bestehende CLI-Backend-Architektur
- `agent/services/_task_scoped_adapters.py` — Task-Scope-Grundlage
