# Ananta TUI User and Operator Guide

## Purpose

Use the TUI for operational workflows: task/artifact handling, approval/audit visibility, KRITIS-oriented monitoring, and repair review.
Current frontend-parity status is tracked in `data/tui_frontend_parity_readiness_report.json`.

## Startup

1. Select a connection profile.
2. Authenticate and confirm runtime header context.
3. Open the relevant view (`tasks`, `approvals`, `logs`, `kritis`, `settings`).

For local MVP runtime smoke:

`python -m client_surfaces.tui_runtime.ananta_tui --fixture`

For compact terminals and explicit section focus:

`python -m client_surfaces.tui_runtime.ananta_tui --fixture --section Tasks --terminal-width 80`

For selected-object drilldown and guarded actions:

`python -m client_surfaces.tui_runtime.ananta_tui --fixture --selected-goal-id G-1 --selected-task-id T-1 --selected-artifact-id A-1`

## Core views

- Dashboard
- Goals
- Tasks
- Artifacts
- Knowledge
- Config
- System
- Teams
- Instruction
- Automation
- Audit
- Terminal
- **Share / Teilnehmer** (Collaboration)
- Help

## Operator safety rules

- Review context before approval actions.
- Prefer dry-run-first where supported for repair flows.
- Use denial/audit views for governance debugging instead of bypassing policy.
- Keep actions explicit and auditable.
- Deep admin and high-risk operations stay browser-first via fallback links.
- Config edits from terminal are allowlisted and require explicit `--apply-safe-config`.
- Task patch/assign/propose/execute actions require explicit `--confirm-task-action`.
- Archived restore/cleanup/delete actions require explicit `--confirm-archived-action`.
- Artifact extract/index actions require explicit `--confirm-artifact-action`.
- Team activation requires explicit `--confirm-team-action`.
- Instruction profile/overlay selection and link/unlink actions require explicit `--confirm-instruction-action`.
- Automation start/stop/tick and planner/trigger configuration require explicit `--confirm-automation-action`.
- Approval review (`--approval-action approve|reject`) requires explicit `--confirm-approval-action`.
- Repair actions stay non-implicit; terminal actions are preview/guard rails and complex execution remains browser-first.
- Artifact upload is intentionally deferred in terminal and handled via browser fallback.

## Navigation

- Keyboard-first navigation model
- Cross-view search/filtering
- Resume state support for profile/last-view continuity
- Navigation shell always shows current section and selected object context.
- Goal/task/artifact/knowledge/template selections are visible in the navigation header.

## Goal/task/artifact workflows

- Goal list/detail includes governance and plan tree context.
- Task workbench includes timeline and logs.
- Orchestration state is read-only in terminal (normal/blocked/failed/stale queues).
- Archived task actions are confirmation-gated.
- Artifact explorer includes detail, extract/index controls, RAG status, and RAG preview.

## Knowledge and templates

- Knowledge collections support inspect, explicit index action, and search (`query`, `top_k`).
- Templates support list/detail, variable registry, sample contexts, validation, diagnostics, and preview.
- Template writes remain browser-first unless a later guarded terminal flow is introduced.

## Teams, instruction layers, automation, audit

- Teams view includes blueprint catalog/detail, team types, role catalog, and role mapping for selected team types.
- Instruction view includes layer model, effective stack resolution, profile list, and overlay list.
- Automation view exposes autopilot/planner/trigger status plus explicit guarded actions (`autopilot_start|stop|tick`, `configure_planner`, `configure_triggers`).
- Audit view includes redacted message rendering, cross-entity references (task/goal/artifact/trace), and analyze summary.
- Approval queue and repair view show pending/stale/denied states, reviewable proposals, and risk-oriented repair status fields.

## Collaboration (Share / Teilnehmer)

Zwei oder mehr TUI-Instanzen können eine sichere Share-Session aufbauen: verschlüsselter Chat, optionale read-only TUI-Ansicht.

### Schnellstart

```bash
# Netzwerkprofil setzen (öffentliche Testinfrastruktur)
export ANANTA_NETWORK_PROFILE=public-ananta

# TUI starten, dann:
:oidc login           # Browser-URL + Code → Account erstellen oder einloggen
:share key generate   # Einmalig: lokalen Device-Key anlegen
:share create         # Session erstellen → gibt Invite-Code aus
```

Anderer Teilnehmer:
```
:oidc login           # eigenen Account
:share key generate
:share join <CODE>    # Invite-Code eingeben
```

Beide sehen sich danach in `:share status`.

### Sicherheitshinweise

- Chat- und View-Payloads werden **vor dem Versand E2E-verschlüsselt**. Kein Server sieht Klartextinhalte.
- Notes bleiben immer lokal (`local_only`). Sie werden nie in eine Share-Session übertragen.
- View-Share ist default deaktiviert. `:share view on` aktiviert ihn explizit und zeigt eine Warnung.
- Remote-Control ist nicht implementiert. Teilnehmer können nur sehen, nicht steuern.
- Device-Key-Fingerprints sind in `:share status` sichtbar und können manuell verglichen werden.

### Befehle

| Befehl | Aktion |
|---|---|
| `:oidc login` | Device Flow starten (Keycloak) |
| `:oidc status` | Anmeldestatus |
| `:oidc logout` | Token löschen |
| `:share status` | Übersicht öffnen |
| `:share key generate` | Lokalen Device-Key erzeugen |
| `:share create [Titel]` | Neue Session + Invite-Code |
| `:share invite` | Invite-Code anzeigen |
| `:share join <CODE>` | Session beitreten |
| `:share view on\|off` | TUI-Ansicht teilen |
| `:share stop` | Session beenden |

Vollständige Doku: `docs/operator-tui-guide.md`, `docs/ops/public-ananta-test-rendezvous.md`

## Live refresh model (optional)

- Manual refresh is always available by rerunning the command.
- Optional polling mode is available with `--live-refresh-target`, `--live-refresh-cycles`, and `--live-refresh-interval-seconds`.
- Supported targets: `system`, `task_logs`, `system_task_logs`.
- Live refresh is explicitly bounded by cycles (stoppable), rate-limited by interval, and shows connection state per cycle.
