# Ananta CLI Commands (User Path)

Diese Seite zeigt den **normalen Nutzerpfad** ueber `ananta ...`.

Voraussetzung: `ananta` ist installiert. Falls der Befehl fehlt, zuerst
`docs/setup/bootstrap-install.md` nutzen.

---

## Schnellstart

```bash
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default
ananta doctor
ananta config show
```

Ziele erstellen (Shortcut-Aliases bleiben erhalten):

```bash
ananta ask "Was sollte ich als naechstes pruefen?"
ananta plan "Bereite den Release-Abschluss vor"
ananta review "Pruefe die Login-Aenderungen"
ananta new-project "Baue ein kleines Release-Check-Tool"
```

Oder explizit ueber die Domain-Struktur:

```bash
ananta goal create "Build a REST API" --profile opencode_preconfigured
ananta goal list
ananta goal inspect           # interaktiver Picker
ananta goal inspect 4c77      # UUID-Praefix
ananta goal status <goal-id>
```

---

## Domain-Gruppen

### `ananta goal` — Ziel-Verwaltung

```bash
ananta goal create "Build a REST API" --profile opencode_preconfigured
ananta goal list
ananta goal inspect                      # interaktiver Picker (Pfeiltasten)
ananta goal inspect <goal-id>
ananta goal inspect 4c77                 # UUID-Praefix reicht
ananta goal status <goal-id>
ananta goal ask "Was sollte ich tun?"
ananta goal new-project "Erstelle ein CLI-Tool"
```

### `ananta config` — Konfiguration

```bash
ananta config show
ananta config show --json
ananta config validate
ananta config export > my-config.json
ananta config setup-planning             # LMStudio-Policy anwenden
ananta config setup-planning --git-workspace --artifact-sync
ananta config apply-profile opencode_preconfigured --dry-run
ananta config apply-profile opencode_preconfigured
```

### `ananta task` — Task-Inspektion

```bash
ananta task inspect                      # interaktiver Picker
ananta task inspect <task-id>
ananta task inspect a1b2                 # UUID-Praefix
ananta task list
ananta task list --goal-id <goal-id>
ananta task list --json
```

### `ananta prompt` — Prompt-Traces und Reports

```bash
ananta prompt inspect --trace-id <id>
ananta prompt render --mode generic --goal "Build a CLI"
ananta prompt goal-traces --goal-id <id>
ananta prompt goal-report --goal-id <id>
ananta prompt goal-flows --goal-id <id>
ananta prompt goal-stuck --goal-id <id>
ananta prompt task-report --task-id <id>
ananta prompt task-why --task-id <id>
ananta prompt artifact-provenance --goal-id <id>
ananta prompt learning-status
ananta prompt planner-profiles
```

### `ananta llm` — LLM-Backends und Logs

```bash
ananta llm list
ananta llm log tail
ananta llm log tail --limit 5
ananta llm log tail --goal-id <id>
ananta llm log tail --json
```

### `ananta hub` / `ananta worker` / `ananta runtime`

```bash
ananta hub status
ananta worker list
ananta runtime list
ananta runtime inspect developer-local
ananta runtime recommend
```

---

## Flat-Commands (bleiben dauerhaft erhalten)

| Befehl | Funktion |
|--------|---------|
| `ananta init` | Ersteinrichtungs-Wizard |
| `ananta first-run` | Interaktiver First-Run-Flow |
| `ananta doctor` | Lokale Umgebungsdiagnose |
| `ananta status` | Hub + Agent Status |
| `ananta update` | Update CLI/Hub |
| `ananta ask "..."` | Shortcut: goal ask |
| `ananta plan "..."` | Shortcut: goal plan |
| `ananta analyze "..."` | Shortcut: goal ask |
| `ananta review "..."` | Shortcut: goal review |
| `ananta diagnose "..."` | Shortcut: goal diagnose |
| `ananta patch "..."` | Shortcut: goal patch |
| `ananta repair-admin "..."` | Shortcut: goal repair-admin |
| `ananta new-project "..."` | Shortcut: goal new-project |
| `ananta evolve-project "..."` | Shortcut: goal evolve-project |
| `ananta llm-log tail` | Alias: ananta llm log tail |
| `ananta tui` | Operator TUI starten |
| `ananta web` | Web-UI URL anzeigen |

---

## Entwickler/CI-Befehle (`ananta dev`)

Nicht fuer Endnutzer. Ersetzen `python scripts/*.py` Aufrufe:

```bash
ananta dev acceptance --scenario-file scenario_lmstudio.json --sla-seconds 900 --password test123
ananta dev check cycles
ananta dev check service-boundaries
ananta dev audit client-surface
ananta dev validate todo-consistency
ananta dev e2e
ananta dev release-gate
ananta dev smoke blender
ananta dev benchmark concurrency
ananta dev latency-diagnostics
```

Vollstaendige Migrationstabelle: `docs/cli/cli_migration.md`

---

## Interaktive ID-Aufloesung

Bei `ananta goal inspect`, `ananta goal status`, `ananta task inspect`:

- **Kein Argument**: Arrow-Key-Picker ueber letzte Goals/Tasks (Pfeiltasten, Enter).
- **UUID-Praefix** (z.B. `4c77`): Auto-Select bei eindeutigem Treffer; Picker bei mehreren.
- **Vollstaendige UUID**: Direktes Lookup.

---

## Hinweise

- `--help` auf jedem Befehl: kein Hub, kein Docker, kein Netzwerk noetig.
- Goal/Task-Befehle: Hub unter `ANANTA_BASE_URL` (default `http://localhost:5000`) noetig.
- Auth: `--user admin --password test123` oder Env-Vars `ANANTA_USER` / `ANANTA_PASSWORD`.
- Execution-Backend: `export SGPT_EXECUTION_BACKEND=ananta-worker` (default) oder `opencode`.

## Weitere Docs

- `docs/cli/cli_inventory.md` — alle Einstiegspunkte klassifiziert
- `docs/cli/cli_taxonomy.md` — Command-Tree und Namensregeln
- `docs/cli/cli_help_contract.md` — Help-Vertrag und Exit Codes
- `docs/cli/cli_migration.md` — Migrationspfade alt -> neu
- `docs/golden-path-cli.md` — Golden Path
