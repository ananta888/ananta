# Augment / Auggie — Installation und sichere Nutzung

## Voraussetzungen

| Komponente | Mindestversion | Zweck |
|---|---|---|
| Node.js | 20+ | Auggie CLI Runtime |
| Auggie CLI | aktuell | Lokale Ausführung |
| Ananta | main | Integration Host |

## Installation

```bash
# 1. Node 20+ installieren (https://nodejs.org)
node --version  # muss v20+ zeigen

# 2. Auggie CLI installieren
npm install -g @augment/cli

# 3. Login (einmalig)
auggie login

# 4. Verify
auggie --version
```

## Sichere Default-Config

Augment ist **standardmäßig deaktiviert**. Alle drei Pfade müssen explizit aktiviert werden:

```yaml
# config/augment.yaml
augment:
  enabled: false  # Master-Switch: false by default
  mcp:
    enabled: false  # MCP Context Provider
  auggie_cli:
    enabled: false  # CLI Worker
    allow_write: false  # Write-Proposal: zusätzlich explizit aktivieren
  interactive_bridge:
    enabled: false  # Interactive Bridge
  security:
    workspace_mode: task_scoped_copy  # IMMER task_scoped_copy für writes
    send_secrets: false  # NIEMALS secrets senden
    require_explicit_project_approval: true
```

## Modi (separat aktivierbar)

### Read-Only Context Provider (MCP)

Augment beantwortet Kontextfragen über `codebase-retrieval`. Keine Schreibrechte.

```yaml
augment:
  enabled: true
  mcp:
    enabled: true
    timeout_seconds: 45
    max_results: 12
```

### Write-Proposal Modus (CLI Worker)

Auggie CLI erzeugt Änderungsvorschläge. Immer mit Task-Copy, nie direktes Repo.

```yaml
augment:
  auggie_cli:
    enabled: true
    allow_write: true  # explizit
  security:
    workspace_mode: task_scoped_copy  # Pflicht für writes
```

### Interactive Bridge

Interaktive Auggie-Sessions. Idle-Timeout und Approval-Gates sind Pflicht.

```yaml
augment:
  interactive_bridge:
    enabled: true
    approval_required_for_write: true  # immer true
    idle_timeout_seconds: 120
```

## Sicherheitsregeln

1. **send_secrets: false** — niemals überschreiben
2. **workspace_mode: task_scoped_copy** — für alle schreibenden Operationen
3. **denied_paths enthält immer**: `.env`, `.git`, `.venv`, `secrets/`, `node_modules/`
4. **require_explicit_project_approval: true** — immer bestätigt lassen
5. **allow_write: false** ist der sichere Default

## Rollback

```bash
# Augment vollständig deaktivieren (kein Neustart nötig)
# Setze in config/augment.yaml:
# augment.enabled: false
```
