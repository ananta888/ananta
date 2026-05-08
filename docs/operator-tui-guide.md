# Ananta Operator TUI Guide

The operator TUI is the new terminal-native client surface for hub operators. It is additive and does not replace the legacy report shell yet.

Start it with:

```bash
ananta tui --operator
```

The legacy shell remains:

```bash
ananta tui
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

## Architecture Boundary

The operator TUI is a client surface. It can inspect hub state, render terminal views, prepare explicit actions, and route requests through hub-owned contracts. It must not orchestrate workers, mutate state directly, or create hidden execution loops.
