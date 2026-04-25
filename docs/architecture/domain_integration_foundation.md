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
