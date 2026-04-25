# Domain Integration Foundation

## Purpose

Ananta needs a shared, generic domain foundation before large integrations (for example Blender, FreeCAD, KiCad) grow into hard-coded backend special cases.

This foundation defines **declarative contracts first** (descriptors, schemas, capabilities, policies, RAG source profiles, bridge contracts) while keeping orchestration, policy, approval and audit centralized in the Hub.

## Scope boundaries

### In scope

- domain descriptor contracts
- domain lifecycle/runtime truth model
- generic registries and validators
- generic policy/approval/audit integration points
- generic bridge adapter contract

### Out of scope

- a plugin marketplace
- arbitrary runtime loading of unreviewed Python modules
- domain-specific orchestration logic inside descriptors
- claiming runtime completeness from descriptor-only wiring

## Generic vs domain-specific responsibilities

### Generic platform layer (Hub-owned)

- descriptor loading and validation
- capability and schema registration
- policy evaluation and approval flow
- audit event recording
- retrieval routing and guardrails
- release-gate runtime truth checks

### Domain layer (descriptor-owned)

- domain metadata and lifecycle/runtime claims
- capability definitions
- context/artifact schema references
- policy and RAG profile references
- bridge adapter type selection

## Security model

- explicit allow-listing for bridge adapter types
- no silent execution path from descriptors to arbitrary code imports
- default-safe policy behavior when descriptors/policies are incomplete
- clear degraded/validation-failed states instead of implicit success

## Lifecycle and runtime truth model

Descriptors and runtime state are separated:

- `lifecycle_status` captures delivery maturity (`planned`, `foundation_only`, `runtime_mvp`, `runtime_complete`, `deferred`, `blocked`, `deprecated`)
- `runtime_status` captures actual executable truth (`descriptor_only`, `runtime_unavailable`, `runtime_degraded`, `runtime_available`)

This separation prevents false runtime claims and gives release gates a stable truth source.

## Why this helps Blender, FreeCAD and KiCad

The foundation allows each integration to reuse the same backend primitives:

- one descriptor model
- one capability model
- one policy/approval model
- one retrieval profile model
- one bridge contract model

That keeps clients thin and avoids repeating backend control-plane logic for each domain.

## SOLID alignment

- **SRP:** descriptors describe; Hub services orchestrate.
- **OCP:** new domains extend by data contracts instead of patching core orchestration paths.
- **DIP:** runtime routing depends on abstract contracts (descriptor/capability/policy schemas), not concrete domain implementations.

## How to add a new domain

1. Add a descriptor at `domains/<domain_id>/domain.json` and validate it against `schemas/domain/domain_descriptor.v1.json`.
2. Add a capability pack at `domains/<domain_id>/capabilities.json` using `capability_pack.v1`.
3. Add context/artifact schemas under `domains/<domain_id>/schemas/` and reference them from the descriptor.
4. Add a domain policy pack under `domains/<domain_id>/policies/` with explicit allow/deny/approval rules.
5. Add one or more RAG source profiles under `domains/<domain_id>/rag_sources/` using `rag_source_profile.v1`.
6. Select an allow-listed bridge adapter type in the descriptor; never load executable code dynamically from domain folders.
7. Register domain status in `data/domain_runtime_inventory.json` with honest `inventory_status`.

## Runtime readiness and release-gate checks

Runtime readiness is not inferred from descriptor presence alone.

- `scripts/audit_domain_integrations.py` validates descriptors, packs, policy references, RAG profiles and runtime inventory consistency.
- `scripts/run_release_gate.py` runs this audit automatically when `data/domain_runtime_inventory.json` exists.
- `runtime_mvp` / `runtime_complete` inventory claims must include runtime files, smoke commands and runtime evidence references.
- Descriptor-only domains are blocked from claiming runtime readiness.

## Safety notes for domain authors

- Never add arbitrary import paths or executable hooks to descriptors.
- Keep policy and approval checks in Hub services, not in domain metadata.
- Treat generated scripts as untrusted artifacts until policy + approval allow execution.

## Consumer roadmap

Blender is the first large consumer of this model, but not the only one.
The same contracts are intended for future FreeCAD and KiCad integrations with domain-specific rules layered on top of the shared foundation.
