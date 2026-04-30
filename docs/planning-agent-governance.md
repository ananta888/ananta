# Planning-Agent Governance Contract

This document captures the active guardrails for delegated planning.

## Core rule

The hub remains the only authority for plan acceptance, persistence, routing, and task materialization.
Planning-agent output is always treated as untrusted proposal input until hub validation passes.

## Planning policy

`planning_policy` (via `POST /config`) controls delegated planning behavior:

- `delegated_planning_enabled`
- `allowed_planner_roles`
- `require_review`
- `allow_remote_planners`
- `max_nodes`
- `max_depth`
- `timeout_seconds`

## Proposal contract

`plan_proposal_contract_version: v1` payloads include deterministic `node_key` entries.
Validation enforces:

- required node list
- unique node keys
- known dependency references
- acyclic dependency graph
- normalized `task_kind` and `risk_level`

## Capability boundary

`planning-agent` capability profile is advisory-only.
Forbidden limits include:

- no plan acceptance
- no plan persistence
- no task materialization
- no worker routing
- no policy mutation

## SOLID notes

- SRP: proposal building/validation is isolated in `planning_proposal_service`.
- OCP: new planner roles and policy modes are additive via config/profile extension.
- DIP: planning routes/services depend on explicit policy and proposal contracts, not on concrete worker internals.
