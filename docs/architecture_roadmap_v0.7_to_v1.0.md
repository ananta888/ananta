# Ananta Architecture Roadmap (v0.7 → v1.0)

This roadmap summarizes the planned evolution of the goal‑driven architecture introduced in the `newway_backend` branch.

---

# Phase 1 — Goal Workflow (v0.7)

Goal → Plan → Task → Execution → Verification → Artifact

Core capabilities:

- goal ingestion API
- hub planning layer
- plan graph generation
- worker delegation
- verification records
- artifact results

Key tasks:

- BE-SEC-773 least privilege artifact and trace access
- BE-SEC-774 tamper‑evident audit logs
- TEST-GOAL-798 security regression tests

Outcome:

The system becomes goal‑first instead of task‑first.

---

# Phase 2 — Governance & Observability (v0.8)

Focus: make the system auditable and inspectable.

Capabilities:

- centralized policy engine
- trace_id propagation across all workflow entities
- artifact lineage tracking
- governance summaries

Key tasks:

- GOV-POLICY-910 policy evaluation layer
- GOV-TRACE-911 unified trace model
- SEC-AUDIT-901 audit hash chaining

Outcome:

Operators can explain *why* every action happened.

---

# Phase 3 — Worker Federation (v0.9)

Focus: scalable distributed workers.

Capabilities:

- worker capability discovery
- routing policies
- queue based orchestration
- retry strategies

Key tasks:

- REL-QUEUE-930 hub→worker task queue
- REL-RETRY-931 structured retry system
- SEC-WORKER-903 strict worker registration validation

Outcome:

The hub becomes a reliable orchestration control plane.

---

# Phase 4 — Multi‑Team Isolation

Focus: safe multi‑tenant operation.

Capabilities:

- team scoped goals
- RBAC roles
- isolated artifacts

Roles:

- admin
- team_admin
- team_member
- viewer

Outcome:

Multiple organizations or teams can use the system safely.

---

# Phase 5 — Autonomous Planning (v1.0)

Focus: advanced planning and reasoning.

Capabilities:

- hierarchical plans
- plan refinement loops
- multi‑step verification
- artifact‑driven outcomes

Safety controls:

- plan node limits
- execution guardrails
- governance checkpoints

Outcome:

The system evolves into a safe autonomous planning platform.

---

# Architectural Principles

Control plane:

Hub

Execution plane:

Workers

Key rules:

- hub never delegates worker→worker
- workers never orchestrate
- all decisions are traceable
- artifacts are the user facing outputs

---

# Target System Model

Goal

→ Plan

→ Plan Nodes

→ Tasks

→ Worker Execution

→ Verification

→ Artifacts

→ User visible results

---

This roadmap reflects the intended direction for the new goal‑driven architecture and provides a guide for future milestones.
---

# Implementation Note (2026-03-26)

A coherent guardrail bundle has been documented and aligned with the roadmap:

- delegate-first hub fallback semantics
- explicit plan depth/node limits
- worker resource guardrails (time/memory/workspace)
- fallback provenance metadata for auditability
- bounded exponential retry with jitter

See `docs/hub-reliability-guardrails.md` for details and rollout guidance.
