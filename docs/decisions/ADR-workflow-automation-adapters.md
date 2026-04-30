# ADR: Workflow Automation Adapters

Date: 2026-04-30
Status: accepted

## Context
Ananta integrates with external automation engines (n8n, Node-RED, webhook runners) for optional workflows.

## Decision
- External workflow engines are optional integration workers.
- Hub remains owner of policy, approval, audit, task state and artifact verification.
- A provider-neutral workflow adapter interface is mandatory.
- n8n is only a reference provider implementation.
- Default mode is disabled and dry-run-first.

## Forbidden usage
- Auto-merge/push without Hub approval.
- Unrestricted shell execution.
- Credential exfiltration in prompts/logs/artifacts.
- Workflow engines acting as source of truth for task plan/state.

## Consequences
- Ananta works without any workflow engine configured.
- Provider additions are additive and isolated by interface.
