# ADR: LLM Interceptor Trust Boundary and API-Key Handling

## Status
Accepted

## Context
Agents and workers need OpenAI-compatible APIs, but direct provider access bypasses Ananta governance and risks context exfiltration.

## Decision
- Upstream provider API keys are stored only in interceptor/hub server-side configuration.
- Workers/agents must not read or receive provider credentials.
- The interceptor is the only allowed egress path when governance is required.
- Trust classes:
  - `local`: higher-trust processing boundary
  - `cloud`: lower-trust sink, default minimized/redacted context

## Threat Model
Primary threat:
- prompt injection that asks the system to disable policy, disable redaction, or forward raw secrets.

Mitigation:
- deterministic policy engine outside prompt space
- fail-closed on enforcement failures
- hard deny for prompt-controlled overrides of routing/policy/redaction

## Consequences
- Better auditability and safer routing controls.
- Slightly higher implementation complexity due to compatibility proxy and validation layer.

