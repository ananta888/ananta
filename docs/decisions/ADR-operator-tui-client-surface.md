# ADR: Operator TUI Client Surface

## Status

Accepted for incremental implementation.

## Context

The current `ananta tui` path is a terminal report surface. It renders a broad snapshot after gathering many hub read-models. That is useful for diagnostics, but it is not a true operator TUI: it has no stable focus model, no modal command line, no section-local loading, and no room for rich markdown or diagram previews.

Ananta's core architecture must remain hub-worker based. The hub owns orchestration, policy, routing, task queue state, and workflow decisions. Workers execute delegated tasks. A terminal UI must not become a hidden orchestrator or a direct mutation path.

## Decision

Add a new operator TUI as an additive client surface under `client_surfaces.operator_tui`.

The existing legacy TUI remains available as the default `ananta tui` path during migration. The new surface is selected explicitly with:

```bash
ananta tui --operator
```

The operator TUI owns only UI concerns:

- app shell layout
- mode and focus state
- keyboard and command contracts
- section selection
- terminal rendering
- command validation before dispatch

The hub continues to own:

- task orchestration
- goal planning
- worker routing
- policy and governance
- task queue state
- mutation approval and audit

Package boundaries:

- `app.py`: CLI entrypoint and app state construction
- `models.py`: focused UI state and contracts
- `sections.py`: section taxonomy and navigation metadata
- `keymap.py`: mode-aware keybinding registry
- `commands.py`: colon-command parsing and dispatch planning
- `renderer.py`: multi-pane terminal rendering

## Framework Direction

The near-term implementation uses a dependency-light terminal renderer so it can ship without changing runtime dependencies. The target richer implementation should evaluate Textual as the primary candidate for async layout, widgets, and testability.

Textual is preferred for the future full TUI because it provides:

- structured panes and focus management
- async refresh primitives
- keyboard bindings
- widget testing support
- a migration path to richer markdown and preview widgets

The dependency-light shell remains useful as a fallback and as a contract test surface.

## UX Model

The operator TUI uses modes:

- `normal`: navigate sections, move focus, refresh, open help
- `command`: enter colon commands such as `:section Tasks` or `:refresh`
- `inspect`: review selected item details and read-only artifacts
- `edit`: reserved for explicit form-like flows

Core sections:

- Dashboard
- Goals
- Tasks
- Artifacts
- Knowledge
- Config
- System
- Audit
- Help

First-class terminal sections should load only their required data. Browser fallback remains appropriate for binary-rich artifacts, deep admin screens, and complex repair sessions.

## Keyboard Contract

Default navigation keys:

- `j` / `k`: move selection down/up
- `h` / `l`: move focus left/right
- `gg` / `G`: first/last item
- `/`: search
- `:`: command line
- `r`: refresh
- `?`: help
- `enter`: inspect/open selected item
- `esc`: close overlay or return to normal mode
- `q`: quit

Risky actions require explicit confirmation and backend validation. The TUI must not treat a keyboard shortcut as sufficient approval for a mutation.

## Migration

Phase 1 keeps the legacy report shell as `ananta tui` and exposes the new shell via `ananta tui --operator`.

Phase 2 adds section-local adapters, markdown rendering, and fixture/live smoke tests.

Phase 3 can promote the operator TUI to default once first-paint, auth, navigation, and key workflows are stable.

Rollback is simple while `--operator` is opt-in: users can continue using the legacy shell or web UI.

## SOLID Check

SRP is protected by separating shell, section taxonomy, keymap, command parsing, and rendering modules.

OCP is protected by adding a new client surface and explicit command selection instead of repeatedly patching the legacy shell.

ISP is protected by small registries and focused data models rather than one broad TUI object.

DIP is protected by keeping action dispatch and future data adapters behind local contracts that can call hub APIs without coupling UI components to route internals.

No new worker orchestration path is introduced.
