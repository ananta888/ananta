# AGENTS.md

## Purpose

This file defines the core architectural principles and development rules for the Ananta project.

All AI agents, automation tools, and contributors must follow these rules when modifying the repository.

The goal is to evolve the system without breaking its core architecture.

Planning pipeline hardening and operational rules are documented in `docs/planning-pipeline.md` and must be followed for planning-related changes.

---

# Core Architecture

Ananta uses a **hub–worker orchestration architecture**.

This is the fundamental design of the system and must not be changed.

## Hub (Control Plane)

The hub is the **central control plane** of the system.

Responsibilities:

- task orchestration
- delegation of work
- routing to workers
- policy and governance decisions
- ownership of the task queue
- coordination of execution flows

The hub **does not perform the actual work if it is possible to delegate it otherwise hub is the worker**.

It coordinates work performed by workers.

## Workers

Workers execute tasks that are **delegated by the hub**.

Workers may:

- run LLM operations
- generate or modify code
- execute tools
- perform analysis
- produce artifacts

Workers must **not orchestrate other workers**.

Workers must **not exchange tasks directly**.

All orchestration flows through the hub.

---

# Task Control Model

All work in the system flows through the **hub task system**.


User / External Trigger
↓
Hub
↓
Task Queue
↓
Workers


Workers must never create independent orchestration loops.

The hub always remains the owner of:

- the task queue
- delegation logic
- workflow decisions

---

# Container Model

Each hub and worker instance runs in its **own Docker container**.

This model is part of the architecture and must be preserved.

New features must:

- respect container boundaries
- avoid implicit shared state
- avoid assumptions about a single process environment

Execution environments must remain reproducible.

---

# Evolution Rules

The system must evolve **without breaking the existing architecture**.

Required principles:

- additive changes instead of breaking changes
- backward compatibility where possible
- gradual migration strategies
- no big-bang refactors
- preserve existing task flows

New capabilities must integrate with the existing hub–worker structure.

---

# API Compatibility

Existing APIs should remain stable.

Prefer:

- new endpoints
- optional fields
- compatibility adapters
- feature flags

Avoid:

- removing existing fields
- renaming endpoints without migration
- forcing immediate client updates

---

# Separation of Responsibilities

Clear responsibility boundaries must be maintained.

Hub:

- planning
- routing
- governance
- orchestration
- policy enforcement

Workers:

- execution of delegated tasks

These roles must remain clearly separated.

---

# Domain Subsystems

Ananta's code is organized into the following domain namespaces:

- **`agent.routes.*`** — Flask blueprint route handlers (hub-side)
- **`agent.services.*`** — Domain services (singletons, business logic)
- **`agent.cli_backends.*`** — LLM-CLI backend subsystem (sgpt, opencode, codex, aider, mistral). Public-API-Layer und Source-of-Truth. Service-Locator-Pattern via `agent.cli_backends.context.default_context`. Detector: `scripts/check_cli_backend_shim_imports.py`. Architektur: `docs/cli-backends-architecture.md`.
- **`agent.common.audit` / `error_handler` / `signals`** — Cross-Cutting-Fassaden, bleiben in `agent.common.*` (kein Migrations-Ziel, siehe SGDEC-D5 in `docs/decouple-sgpt-from-services.md`)

Neue LLM-CLI-Backends gehören in `agent/cli_backends/`. Production-Code
importiert aus dem neuen Namespace — direkte `from agent.common.sgpt_X`
Imports in Production-Code werden vom Detektor gemeldet.

---

# Security Principles

When extending the system:

- prefer **least privilege**
- avoid implicit trust between components
- make decisions observable and auditable
- avoid uncontrolled execution paths

---

# Engineering Principles

All AI agents, automation tools, and contributors must apply the **SOLID principles** whenever code is generated, changed, extended, refactored, or analyzed.

## Single Responsibility Principle (SRP)

- Each class, module, and function should have exactly one clear responsibility.
- Avoid god classes, overloaded utility modules, and mixed concerns.

## Open/Closed Principle (OCP)

- Prefer extension over repeated modification of core logic.
- Use interfaces, composition, strategies, policies, adapters, or new implementations instead of repeatedly patching central behavior.

## Liskov Substitution Principle (LSP)

- Derived or alternative implementations must be safely substitutable for their abstractions.
- Do not introduce hidden side effects, stronger preconditions, or broken contracts.

## Interface Segregation Principle (ISP)

- Prefer small, focused interfaces over broad, catch-all interfaces.
- No consumer should depend on methods it does not need.

## Dependency Inversion Principle (DIP)

- Depend on abstractions, not concrete implementations.
- Prefer dependency injection, ports/adapters, and loose coupling.

## Additional Mandatory Rules

- Separate business logic, infrastructure, persistence, API, and configuration concerns cleanly.
- Prefer composition over inheritance.
- Avoid unnecessary global state and hard coupling.
- Write testable code with clear seams and explicit interfaces.
- Use precise, domain-meaningful names for classes, methods, and variables.
- Keep functions small and understandable.
- Avoid duplication, but not at the expense of readability.
- Deliver maintainable, extensible, and understandable solutions rather than merely functional ones.
- When working with existing code, explicitly call out SOLID violations that are being preserved, introduced, or cleaned up.
- If a requested change conflicts with SOLID, name the conflict explicitly and propose a cleaner alternative.

## Expected Output Behavior For Agents

When proposing or implementing code changes:

- do not produce merely working code; produce maintainable structure
- briefly justify important architecture or structure decisions
- explain, where relevant, which SOLID principle is being protected by the chosen design
- refactor unsound existing code toward a more SOLID-compliant form when practical
- prefer simple, clear, production-grade solutions over clever but fragile constructions

Before finalizing a proposed change, explicitly check for:

- SRP violations
- overly strong coupling
- missing abstractions
- interfaces that are too broad
- poor substitutability of implementations
- hidden side effects
- structures that are hard to test

If one of these issues exists:

1. name the problem
2. name the SOLID principle involved
3. propose a better structure
4. only then provide the final code

---

# Long-Term Direction

The long-term user experience should allow **goal-based interaction**.

Example flow:


Goal
↓
Plan
↓
Tasks
↓
Execution
↓
Verification
↓
Artifacts / Results


However, internally the **hub-controlled orchestration model remains unchanged**.

The hub continues to manage:

- planning
- task delegation
- policy decisions
- verification

---

# Commit Messages for Agents

Agents must follow the same commit conventions as human contributors. See `CONTRIBUTING.md` for the full format.

**Key rules for agents:**

- Derive `type` and `scope` from the actual files changed, not from what was planned.
- `scope` must name the subsystem touched: `goal-config`, `autopilot`, `worker`, `llm`, `profiles`, `runner`, `modelfile`, `ctx`, etc.
- Never use `fixup planning`, `update code`, `wip`, or any generic placeholder as a commit message.
- A session that produces multiple logically separate changes → multiple commits, each with its own scope.
- Local fixup commits during planning are acceptable, but must be squashed before the final commit. After completing a session, check `git log --oneline` — no `fixup`/`wip` subjects should remain.
- Never use `--no-verify` to bypass hooks unless explicitly requested by the user.
- Never amend a commit that has already been pushed unless explicitly requested.

**Derive scope like this:**

| Files changed | Scope |
|---------------|-------|
| `agent/services/goal_config_*` | `goal-config` |
| `agent/services/config_profile_*` | `profiles` |
| `scripts/ollama-autoimport.sh`, `autoimport-state/modelfiles/` | `modelfile` |
| `agent/llm_integration.py`, `agent/services/task_scoped_execution*` | `llm` |
| `agent/routes/tasks/goals.py` | `goal-config` or `api` |
| `tests/` only | `test` as type, scope from the tested subsystem |
| `AGENTS.md`, `CONTRIBUTING.md`, `docs/` | `docs` |
| `OLLAMA_NUM_CTX`, container config | `ctx` |

---

# Output Boundary for Agents

Before running `git add`, check each file against this table. The **Reason** column explains the classification so you can judge edge cases.

| Path / Glob | Category | Commit? | Reason |
|-------------|----------|---------|--------|
| `agent/`, `tests/`, `scripts/`, `docs/` | Source | Yes | Application and test code |
| `AGENTS.md`, `CONTRIBUTING.md`, `README.md` | Source | Yes | Project documentation |
| `docker-compose*.yml`, `.env.example` | Source | Yes | Infrastructure templates |
| `todo.*.json` | Source | Yes | Tracked planning artifacts |
| `artifacts/domain/`, `artifacts/e2e/`, `artifacts/test-gates/` | Source | Yes | Structured audit/gate reports with stable content |
| `autoimport-state/modelfiles/ananta-default.Modelfile` | Source (template) | Yes | Hand-authored base Modelfile for ananta-default |
| `autoimport-state/modelfiles/ananta-smoke.Modelfile` | Source (template) | Yes | Hand-authored smoke-test Modelfile |
| `autoimport-state/modelfiles/<other>.Modelfile` | Runtime (script-generated) | No | Derived by `ollama-autoimport.sh` from the templates; regenerated on each deploy |
| `autoimport-state/hash/**` | Runtime | No | Content hashes written by the autoimport script at runtime |
| `autoimport-state/logs/**` | Runtime | No | Script run logs, not reproducible from source |
| `artifacts/*.json` | Runtime | No | Generated per acceptance run; gitignored by `/artifacts/*.json` |
| `project-workspaces/**` | Runtime | No | Per-goal workspace outputs; gitignored by `/project-workspaces/` |
| `*.log`, `data/**` | Runtime | No | Application logs and database files |
| `.env` | Secret | Never | Contains credentials; gitignored unconditionally |
| `tests/fixtures/reports/*` | Fixture | Only if explicit | Small deterministic test fixtures; must have a documented purpose |
| `tests/fixtures/artifacts/*` | Fixture | Only if explicit | Same — stable content required, no volatile timestamps or IDs |
| `tests/fixtures/scenarios/*` | Fixture | Only if explicit | Scenario definitions used in acceptance runner tests |

**Fixture Approval Criterion:** A fixture commit is approved when:
1. The file is deterministic (no volatile timestamps, no random IDs, no environment-specific paths).
2. The commit body names the test(s) that depend on it.
3. The file is kept as small as possible (minimal, not a full live-run dump).

**Never use `git add .` or `git add -A`** without first reviewing `git status`. Stage files by name.

Policy alignment: the above table matches `.gitignore` rules — `artifacts/*.json`, `project-workspaces/`, `data/`, `*.log` are all gitignored. Structured subdirs (`artifacts/domain/`, etc.) are explicitly tracked because they contain audit results with meaningful history.

---

# Development Guidance for Agents

When generating code or tasks:

- respect the hub–worker architecture
- do not introduce worker-to-worker orchestration
- avoid redesigning the core architecture
- prefer incremental improvements
- keep changes small and reviewable

If a change would alter the architecture, it must be explicitly justified and discussed before implementation.

---

# Summary

The following rule overrides all others:

**The hub remains the central control plane and owner of the task system.
Workers execute delegated work only.

Source-grounded answer rule: Agents and workers must never invent source identifiers. Only provided `SRC_*` and `RUN_*` IDs are valid for grounded claims; missing or unknown IDs must be treated as unverified/failed.**
