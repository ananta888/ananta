# CodeCompass Context Trust Model

CodeCompass-derived records are untrusted prompt input.

## Rules

1. Retrieved text is data, not instruction authority.
2. Task instructions and policy constraints remain the trusted control plane.
3. Prompt-injection-like content in comments/docs/XML/config is quoted and treated as evidence only.
4. Sensitive values (tokens, API keys, secrets, absolute paths) are redacted before prompt assembly.

## Guardrails

- Retrieval content passes through redaction before assembly.
- Instruction-override markers are filtered as blocked context chunks.
- Hostile text does not mutate command policy or approval requirements.

