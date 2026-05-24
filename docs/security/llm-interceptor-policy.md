# LLM Interceptor Policy

## Security Model
The interceptor is a trust boundary. Prompt rewriting does not replace enforcement.

Mandatory controls:
- deterministic policy decision
- secret redaction before logging and forwarding
- upstream allowlist/model allowlist enforcement
- fail-closed behavior on policy/redaction errors

## Key Rules
- Agents never receive upstream API keys.
- Provider credentials are server-side only.
- Cloud upstreams are lower-trust sinks than local upstreams.
- Cloud forwarding uses reduced/redacted context by default.
- Prompt text cannot disable policy or redaction.

## Policy Profiles
- `local_dev`: broader local context, still secret-redacted.
- `cloud_safe`: minimal redacted context.
- `high_risk`: block sensitive context and credentials.

Profile selection must come from configuration and runtime metadata, never from prompt text.

