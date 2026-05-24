# OpenCode with Ananta LLM Interceptor

## Goal
Configure OpenCode to call only the Ananta OpenAI-compatible interceptor endpoint.

## Example
- Provider: `@ai-sdk/openai-compatible`
- Base URL: `http://127.0.0.1:8787/v1`
- Model alias: `intercepted-coder`

See:
- `deploy/examples/opencode.ananta-interceptor.json`

## Important
- Upstream API/provider keys are configured only in interceptor server config.
- OpenCode must not receive provider keys.
- Alias `intercepted-coder` maps to routed upstream/model internally.
- Direct provider base URLs bypass interceptor policy and should be avoided for governed runs.

## Troubleshooting
- If `model_not_allowed_for_upstream` occurs: check interceptor allowlist and alias map.
- If `policy_denied` occurs: verify task metadata/profile and cloud/local policy profile.
- If 404 at `/v1/*`: confirm interceptor bind host/port/prefix.
