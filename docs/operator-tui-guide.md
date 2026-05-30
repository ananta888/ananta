# Ananta Operator TUI Guide

The operator TUI is the default terminal-native client surface for hub operators.

Start it with:

```bash
ananta tui
```

The legacy report shell remains available for compatibility:

```bash
ananta tui --legacy
```

For scripts, tests, and non-interactive captures, render a single frame:

```bash
ananta tui --render-once
```

## Authentication

The operator TUI reads the same local environment conventions as the CLI:

```bash
export ANANTA_BASE_URL=http://localhost:5000
export ANANTA_USER=admin
export ANANTA_PASSWORD='your-password'
```

For token-first sessions, set:

```bash
export ANANTA_AUTH_TOKEN='your-token'
```

The current operator shell exposes auth state in the status line. Backend mutation still belongs to the hub; the TUI only prepares and dispatches explicit requests.

## Core Keys

- `j` / `k`: move selection down/up
- `h` / `l`: move focus left/right
- `gg` / `G`: first/last item
- `/`: search entry point
- `:`: command entry point
- `r`: refresh active section
- `?`: help
- `enter`: inspect selected item
- `esc`: cancel modal state
- `q`: quit

## Commands

- `:section <id>` opens a section.
- `:refresh` requests a section refresh.
- `:focus navigation|content|detail` moves focus.
- `:inspect` opens read-only inspection mode.
- `:action <name> <risk>` prepares an explicit action.
- `:confirm` confirms a pending risky action.
- `:cancel` clears pending action or modal state.
- `:browser [target]` prints the section-aware browser fallback URL.

Risky actions require explicit confirmation. The TUI does not bypass hub policy, approval, audit, or mutation gates.

## Markdown And Diagrams

Markdown source can be previewed with:

```bash
ananta tui --operator --section artifacts --markdown-source '# Title'
```

Mermaid and PlantUML blocks are detected and rendered through a text fallback first. Rich inline image rendering is optional and depends on terminal support.

Supported capability probes:

- kitty graphics via `KITTY_WINDOW_ID`
- iTerm2 inline images via `TERM_PROGRAM=iTerm.app`
- sixel via `TERM` containing `sixel`

When no graphics protocol is detected, the TUI uses text diagram fallback.

## Smoke And Performance

Fixture smoke:

```bash
ananta tui --operator --smoke
```

First-paint measurement:

```bash
ananta tui --operator --measure-first-paint
```

The default first-paint budget is intentionally small so regressions in terminal startup become visible early.

## Rollout

The operator TUI is opt-in through `ananta tui --operator`.

Optional rollout controls:

```bash
export ANANTA_OPERATOR_TUI_ENABLED=1
export ANANTA_OPERATOR_TUI_STAGE=local_dev
```

Rollback:

```bash
export ANANTA_OPERATOR_TUI_ENABLED=0
ananta tui
```

Rollout stages:

- `local_dev`
- `advanced_opt_in`
- `default_candidate`
- `default`

## Collaboration: Share / Teilnehmer

### OIDC-Login in der TUI

Für die öffentliche Testinfrastruktur oder jedes Keycloak-Deployment mit Device Authorization Grant:

```
:oidc login      – startet Device Flow, zeigt URL + Code an
:oidc status     – aktueller Login-Status und Username
:oidc logout     – Token verwerfen
```

Der Flow läuft vollständig im Terminal:

1. `:oidc login` zeigt eine URL und einen kurzen Code an.
2. URL im Browser öffnen, Code eingeben, Account erstellen oder einloggen.
3. TUI empfängt Token automatisch im Hintergrund — kein weiterer Schritt nötig.

Voraussetzung: `ANANTA_NETWORK_PROFILE=public-ananta` (oder eigenes Keycloak mit Device Grant).

### Share-Session

```
:share status               – Übersicht: OIDC, Device-Key, aktive Sessions
:share key generate         – lokalen Device-Key erzeugen (einmalig)
:share key show             – Fingerprint anzeigen
:share key rotate           – Key rotieren (invalidiert alte Sessions)
:share create [Titel]       – neue Share-Session erstellen, gibt Invite-Code aus
:share invite               – Invite-Code der aktiven Session anzeigen
:share join <CODE>          – per Invite-Code beitreten
:share view on|off          – TUI-Ansicht für Teilnehmer freigeben / sperren
:share stop                 – Session beenden
:share help                 – alle Share-Befehle
```

Die Share-Section (`s` im Navigationsmenü oder `:section share`) zeigt OIDC-Status, Device-Key-Fingerprint, aktive Sessions und Teilnehmerliste live.

### Derselbe Account auf mehreren Umgebungen

Ein Keycloak-Account kann parallel aus mehreren TUI-Umgebungen derselben Share-Session beitreten. OIDC identifiziert den User, der lokale Device-Key/Fingerprint identifiziert die konkrete Umgebung. Dadurch erscheinen Laptop, Container oder VM als getrennte Teilnehmer, solange jede Umgebung einen eigenen Device-Key hat.

Für jede Umgebung:

```
:oidc login
:share key generate
:share join <CODE>
:share status
```

Wenn eine Umgebung aus einem geklonten Workspace stammt und denselben Device-Key verwendet, rotiere den Key in einer der Umgebungen mit `:share key rotate`. Private Device-Keys sollen nicht zwischen Umgebungen kopiert werden.

### Netzwerkprofil

| Profil | Beschreibung |
|---|---|
| `local` (Standard) | Eigene Infrastruktur, kein öffentlicher Rendezvous |
| `public-ananta` | Test-Infrastruktur auf `keycloak.ananta.de` + `webrtc.ananta.de` |
| `offline` | Kein Netzwerk |
| `custom` | Eigene Endpunkte per ENV |

```bash
export ANANTA_NETWORK_PROFILE=public-ananta
```

Alle Payloads (Chat, TUI-View) werden vor dem Versand E2E-verschlüsselt. Der Rendezvous-Server sieht nur verschlüsselte Blöcke und Session-Metadaten.

## Architecture Boundary

The operator TUI is a client surface. It can inspect hub state, render terminal views, prepare explicit actions, and route requests through hub-owned contracts. It must not orchestrate workers, mutate state directly, or create hidden execution loops.
