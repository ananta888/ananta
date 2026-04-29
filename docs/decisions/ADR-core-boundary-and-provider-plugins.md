# ADR: Core Boundary and Provider Plugin Architecture

## Status

Accepted

## Context

Ananta is growing across many domains and integrations: native workers, external coding tools, RAG, MCP, workflow automation providers, Blender, KiCad, FreeCAD, web/TUI/CLI clients, Evolver, research providers and future domain plugins.

Without a strict boundary, the core can become a Medusa: every integration adds direct imports, special cases, configuration branches and runtime assumptions into the central Hub. That would make Ananta hard to reason about, hard to secure, hard to test and hard to run in local-dev mode.

The desired architecture is not to reduce capability, but to keep the core small and push domain-specific behavior behind explicit provider/plugin contracts.

## Decision

Ananta Core must only know stable platform concepts:

- Task
- Goal
- Plan
- PlanNode
- Artifact
- Capability
- Policy
- Approval
- Audit
- Worker
- Adapter/Provider contracts
- Runtime profile

Ananta Core must not directly know provider internals such as:

- Blender scene internals
- KiCad netlist internals
- FreeCAD constraint internals
- n8n workflow internals
- Node-RED flow internals
- GitHub/Jira/Confluence provider internals
- OpenCode/Aider/Copilot-specific execution details

Provider-specific logic must live behind explicit adapter interfaces and be disabled by default unless configured.

## Core Rule

A provider integration is valid only if it can be removed or disabled without breaking Ananta Core startup, core tests or local-dev usage.

If disabling a provider breaks core planning, core task handling, core policy or core artifact handling, the integration is incorrectly coupled.

## Provider Families

### Domain Graph Providers

Examples:

- Blender
- FreeCAD
- KiCad

Core-facing contract:

- Input: project/file/export reference
- Output: provider-neutral DomainGraphArtifact
- Optional: validation report, provenance, warnings

Ananta Core should see DomainGraphArtifact, not BlenderScene/KiCadNetlist/FreeCADDocument internals.

### Workflow Automation Providers

Examples:

- generic webhook provider
- n8n
- Node-RED
- Activepieces
- Huginn
- Windmill

Core-facing contract:

- WorkflowDescriptor
- capability/risk metadata
- dry-run support
- execution request
- callback/result artifact

Workflow providers must not own Ananta task/plan state.

### Worker Execution Providers

Examples:

- ananta-worker
- OpenCode
- Aider
- GitHub Copilot CLI-like tools
- ShellGPT-like tools

Core-facing contract:

- WorkerJob
- allowed capabilities/tools
- context bundle
- expected output schema
- result artifact
- policy decision reference

Worker providers must not bypass approval/policy gates.

### Research/Evolution Providers

Examples:

- DeerFlow
- Evolver
- future research engines

Core-facing contract:

- ResearchRun/EvolutionRun
- source/context references
- proposal artifacts
- confidence/risk metadata
- review-required state

They may propose changes but must not directly apply code without Hub approval.

## Dependency Direction

Allowed:

```text
Core -> provider interface
Provider implementation -> provider-specific dependency
```

Forbidden:

```text
Core -> Blender/n8n/KiCad/FreeCAD/OpenCode-specific imports
Client -> provider-specific orchestration
Provider -> direct privileged Hub internals without scoped API/policy
```

## Feature Flags and Runtime Profiles

All non-core providers must be:

- disabled by default
- enabled by runtime profile/config
- testable through mocks or dry-run mode
- safe to omit from dependencies where possible

Local-dev must work without optional providers installed.

## Artifacts as Boundary Objects

Provider-specific data must be converted into provider-neutral artifacts before it enters the normal Hub flow.

Examples:

```text
Blender scene export -> DomainGraphArtifact
KiCad PCB/netlist -> DomainGraphArtifact
n8n workflow run -> WorkflowIntegrationRunArtifact
OpenCode execution -> PatchArtifact / WorkerResultArtifact
Evolver proposal -> EvolutionProposalArtifact
```

Core policies, approvals, logs and UI should primarily consume these neutral artifact contracts.

## Testing Requirements

Every provider family must have:

- interface contract tests
- mock provider tests
- disabled-provider tests
- dry-run tests where applicable
- release-gate checks that core still starts without provider dependencies

Provider-specific live integration tests must be optional and not part of the default core test path.

## Consequences

Positive:

- Core stays small and safer.
- Integrations can grow without turning Ananta into a Medusa.
- Providers can be swapped or removed.
- Tests become clearer.
- OSS local-dev remains lightweight.

Negative / Cost:

- More adapter/interface code.
- Some duplication in provider-specific mapping layers.
- Slower initial integration for new domains.
- Requires discipline to avoid direct imports and special cases.

## Enforcement

Future todos and implementations must avoid direct provider coupling in core modules.

Operational references:

- `docs/development/provider-plugin-guide.md`
- `docs/architecture/medusa-risk-checklist.md`
- `config/core_provider_boundary.json`

Release gates should eventually detect:

- provider-specific imports in core packages
- optional provider dependencies required for core startup
- missing mock/dry-run provider tests
- provider-specific data structures leaking into core Plan/Task/Artifact paths

## Relationship to Existing Tracks

This ADR applies to:

- planning/blueprint cleanup
- workflow automation adapter layer
- Blender/FreeCAD/KiCad graph ingestion
- Ananta worker/provider work
- MCP/external tool integration
- Evolver/DeerFlow integrations

The architectural rule is simple:

```text
Core owns policy/state/contracts.
Providers own domain-specific execution and translation.
Artifacts carry results across the boundary.
```
