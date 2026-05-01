# Eclipse Plugin Adapter Foundation

This document captures the first implementation block of the Eclipse plugin adapter track.

## Current runtime decision (EAC-T33..EAC-T51 runtime phase)

- Current track decision: Eclipse runtime is **unblocked for headless/bootstrap command/view MVP delivery**.
- Current state: foundation contracts/tests/docs remain valid, runtime command/context/view models exist, and Eclipse platform adapter classes are present.
- Runtime status truth: Eclipse plugin and views remain `runtime_mvp`, scoped to headless/bootstrap evidence until installed Eclipse UI automation is available.
- Runtime artifacts:
  - Eclipse plugin project with `plugin.xml`
  - `META-INF/MANIFEST.MF`
  - deterministic dockerized Gradle build command
  - runtime command registry and views extension registry
  - Eclipse `AbstractHandler` and `ViewPart` adapter classes
  - runtime smoke script

See `docs/eclipse-plugin-runtime-bootstrap.md` for build and smoke commands.

## Thin adapter boundary

- Eclipse plugin is a UI adapter, not a control plane.
- Planning, routing, governance, approval policy and orchestration stay in Ananta.
- Plugin commands map to existing Ananta goal/task flows.

## Minimum support baseline

- Eclipse distribution baseline is explicitly modeled.
- Java baseline and required dependencies are tracked in one support matrix.
- Local development setup expectations are part of the contract.

## Core workflows

- Connection profile and auth support
- Health and capability handshake
- Workspace/project context and editor selection handoff
- Core command set: analyze, review, patch, new-project, evolve-project
- Goal input panel with official-use-case presets
- Task/artifact listing, refresh, diff/review rendering, review/approval actions
- Open-in-browser shortcuts for deeper web UI inspection

## Guardrails

- Context payloads are bounded and user-visible.
- Secrets are redacted and never logged.
- Proposal rendering is review-first and does not auto-apply changes.
- Optional SGPT/CLI bridge is secondary and bounded, without direct shell assumptions.

## Extended safety and rollout layer

- OpenAI fallback and MCP path are captured as explicit evaluations rather than default integration paths.
- Context packaging, security/privacy guardrails, degraded-mode handling and trace visibility are modeled in dedicated contracts.
- First-run UX, golden path demo, manual smoke checklist and future roadmap are tracked as rollout artifacts.

See `docs/eclipse-plugin-adapter-evaluation-and-rollout.md` for the evaluation and rollout details.
For the views extension foundation, see `docs/eclipse-plugin-views-extension-foundation.md`.
