# Backend Provider Contracts

Ananta uses one contract vocabulary for local, hosted and remote providers. The goal is routing consistency, not hiding important policy differences.

## Contract Version

- Version: `v1`
- Catalog builder: `agent.backend_provider_contracts.build_backend_provider_contract_catalog`

## Required Fields

Every provider contract includes:

- `provider`
- `provider_type`: `local_openai_compatible`, `remote_ananta`, `hosted_api`, `cli_backend` or `local_voice_runtime`
- `location`: `local`, `remote` or `hosted`
- `transport`
- `capabilities`
- `routing`
- `governance`
- `health`

## Routing Rule

Local, hosted and remote providers must expose the same eligibility, governance and health fields before routing decisions. Remote providers may still have stricter policy requirements such as `remote_hub_policy`, `max_hops` or trust-level checks.

## Current Contract Families

| Provider | Type | Location | Main use |
| --- | --- | --- | --- |
| `ollama` | `local_openai_compatible` | local | local inference |
| `lmstudio` | `local_openai_compatible` | local | local OpenAI-compatible inference |
| `ananta_remote` | `remote_ananta` | remote | governed remote hub access |
| `codex_cli` | `cli_backend` | local | task-scoped execution |
| `hosted_openai` | `hosted_api` | hosted | hosted inference |
| `voice_runtime` | `local_voice_runtime` | local | voice transcription and command extraction |

## Stable Voice Capability Names

The voice contract uses stable capability names so clients remain backend-agnostic:

- `audio_input`
- `transcription`
- `voice_command`
- `multimodal_audio_prompt`

## Boundary

This contract does not merge execution and inference semantics. It gives routing and governance a common shape so special cases remain explicit and testable.
