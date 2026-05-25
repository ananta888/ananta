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
| `ananta tui --open <file>` | Datei im konfigurierten Editor oeffnen |
| `ananta tmux edit <file>` | Alias fuer `tui --open` |
| `ananta tmux tool <tool-id>` | TUI-Tool starten (z.B. lazygit, ranger) |
| `ananta web` | Web-UI URL anzeigen |

---

## `ananta tui` — Embedded Editor & TUI Tools

```bash
# Datei im Standard-Editor (Vim) oeffnen
ananta tui --open README.md

# Datei in einem bestimmten Editor oeffnen
ananta tui --open app.py --with nvim

# Datei schreibgeschuetzt oeffnen
ananta tui --open config.json --readonly

# TUI-Tool starten
ananta tui --tool git_ui          # lazygit
ananta tui --tool file_manager    # ranger

# Workspace explizit angeben (default: aktuelles Verzeichnis)
ananta tui --open app.py --workspace /path/to/project
```

### Splash / Startup-Animation

Beim Start zeigt die Operator TUI standardmaessig das Ananta-Logo fullscreen an.
Nach 2 Sekunden geht es fließend in einen kompakten 8-zeiligen Header ueber
(Logo links, Status rechts).

| CLI Flag | Default | Beschreibung |
|----------|---------|-------------|
| `--skip-splash` | on | Fullscreen-Splash deaktivieren. Zeigt sofort den kompakten Header (Standard). |
| `--splash` | off | Fullscreen-Splash explizit aktivieren. |
| `--splash-seconds` | `2.0` | Dauer der Fullscreen-Phase in Sekunden. |

| Umgebungsvariable | Wirkung |
|-------------------|---------|
| `ANANTA_TUI_SPLASH=0` | Splash komplett deaktivieren (wie Standard/`--skip-splash`). |
| `ANANTA_TUI_SPLASH=1` | Splash erzwingen (wie `--splash`). |
| `NO_COLOR=1` | Keine ANSI-Farben im Splash/Header. |

Waehrend der Fullscreen-Phase kann jede Taste gedrueckt werden, um sofort
zum kompakten Header zu springen.

Vorschau ohne interaktive TUI:
```bash
# Standard ohne Splash
ananta tui --render-once --skip-splash --width 120 --height 32

# Fullscreen-Logo + Shell (nach splash-seconds automatisch)
ananta tui --render-once --splash --width 120 --height 32
```

Editor-Aufloesung (Reihenfolge, erster Treffer gewinnt):

1. `--with <editor>` Argument
2. Projekt-Filetype-Regel (`.ananta/tui-tools.json`)
3. User-Filetype-Regel (`~/.config/ananta/tui-tools.json`)
4. Global-Filetype-Regel
5. `$EDITOR` / `$VISUAL` (wenn `allow_environment_editor: true`)
6. `default_editor` aus Config
7. Fallback: `vim`

## `ananta tmux` — Terminal-Shortcuts

```bash
# Datei bearbeiten
ananta tmux edit README.md
ananta tmux edit app.py --with nvim
ananta tmux edit config.json --readonly
ananta tmux edit app.py --workspace /path/to/project

# TUI-Tool starten
ananta tmux tool git_ui
ananta tmux tool file_manager
```

`ananta tmux` ist ein Alias-Einstiegspunkt fuer dieselbe Editor/Tool-Logik wie
`ananta tui --open` und `ananta tui --tool`. Beide Befehle verwenden denselben
Resolver und dieselbe Workspace-Validierung.

Konfiguration: `docs/configuration/tui-tools.md`  
Sicherheitsmodell: `docs/security/terminal-sessions.md`

## `ananta ssh` — Native SSH terminal path

```bash
ananta ssh login
ananta ssh targets
ananta ssh connect --target-type worker --target-id alpha
ananta ssh connect --target-type hub --reason "incident triage"
```

Hinweise:
- Nutzt die bestehende OIDC-Integration (`/auth/oidc/*`) fuer Identity-Binding.
- Benoetigt `NATIVE_SSH_ENABLED=true` und einen konfigurierten SSH-CA-Backend-Pfad.
- Hub-Zugriff ist getrenntes High-Risk-Recht und standardmaessig denied.
- Details: `docs/security/ssh-terminal-access.md`.

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
