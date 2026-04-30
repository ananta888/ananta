# Workflow Automation Adapters

Date: 2026-04-30

Workflow automation adapters are optional integration workers.

- Provider-neutral descriptor + registry + policy check.
- Generic webhook + mock providers are baseline.
- n8n is an optional provider behind the same interface.

Smoke path (no live n8n required):
- load descriptors
- policy allow/block
- dry-run execution
- callback parse/auth
