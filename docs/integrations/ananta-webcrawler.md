# External ananta-webcrawler provider

Ananta integrates `ananta-dev-solutions/ananta-webcrawler` as an external
service. It does not copy or reimplement Playwright control, recording,
replay, session refresh, profile generation, or credential storage. The Hub
owns routing, policy, lifecycle authorization, and audit; workers only execute
Hub-delegated calls through the adapter.

This integration is separate from CodeCompass. CodeCompass supplies code and
project context. The Webcrawler supplies bounded web, browser, replay, and
website-AI results.

## HTTP contract

The configured `base_url` points at the OpenAI-compatible `/v1` root.

- `GET /v1/models` lists profiles. A returned model `id` is a Webcrawler
  profile/integration name, not necessarily an LLM model.
- `POST /v1/chat/completions` executes a profile. `stream: false` returns a
  normal OpenAI-compatible completion. `stream: true` returns SSE `data:`
  chunks terminated by `data: [DONE]`.
- `tool_results`, when present at response or message level, are treated as
  structured, redacted evidence and diagnostics.

Stable error mapping is: 404 `webcrawler_profile_not_found`, 409
`webcrawler_profile_draft`, 422 `webcrawler_profile_invalid`, authentication
failures as `webcrawler_authentication_failed`, and 5xx as
`webcrawler_execution_failed`.

## Configuration and deployment

The safe default in `config.example.yaml` is disabled. No service is started
unless both a managed mode and `managed_lifecycle_enabled: true` are explicit.
An API key is referenced by environment-variable name; the value is never
stored in provider configuration or audit output.

For an already running service:

```yaml
providers:
  ananta_webcrawler:
    enabled: true
    mode: external_url
    base_url: https://webcrawler.internal.example/v1
    api_key_env: ANANTA_WEBCRAWLER_API_KEY
    roles: [backend_provider, tool_provider]
    policy_mode: strict
    fallback_policy: semantic_match_only
```

For local development, `managed_process` additionally requires an absolute
`repo_path`, an argv-list `startup_command`, and explicit lifecycle enablement.
The command is executed without a shell and no Webcrawler modules are imported.

```yaml
mode: managed_process
base_url: http://127.0.0.1:8787/v1
repo_path: /srv/ananta-webcrawler
startup_command: [python, -m, ananta_webcrawler]
managed_lifecycle_enabled: true
```

For Compose, use an absolute Compose file and one service name:

```yaml
mode: managed_docker_compose
base_url: http://127.0.0.1:8787/v1
docker_compose_file: /srv/ananta-webcrawler/compose.yaml
docker_compose_service: webcrawler
managed_lifecycle_enabled: true
```

Lifecycle calls are separate from chat/tool execution and require a positive
Hub policy decision. That decision may be issued automatically by configured
policy; no interactive human step is required by the runtime or its tests.

## Routing and policy

Automatic selection is limited to configured tags such as `web`, `browser`,
`replay`, `website_ai`, and `external_api_wrapper`. Code/refactoring tasks are
not routed to this provider unless it is explicitly selected with an allowed
profile. Global blind fallback is unsupported.

The default tools are `webcrawler.list_profiles`, `webcrawler.run_profile`,
and `webcrawler.get_profile_status`. Recording and profile mutation tools stay
inactive unless separately enabled. The central ananta-worker registry knows
their schemas, while the provider catalog and execution adapter enforce the
configured role and feature switches. Read actions are low risk. Login, session,
click, replay, form submission, posting, deletion, booking, and ordering are
high or critical and require an affirmative Hub policy authorization.

The worker cannot self-authorize by adding a tool argument. For unattended
operation, the Hub supplies a separate trusted `webcrawler_policy_context`
after its policy or persisted goal pre-approval grants the exact action. This
supports fully automated runs without weakening the default deny behavior.

Audits contain only profile, endpoint, mode, action, policy decision,
duration, and success/failure. Cookies, tokens, passwords, credentials, and
raw browser sessions are not accepted as audit fields.

The Hub's `ToolRoutingService` exposes the semantic routing decision and its
reason code. Chat and worker backend catalogs use the same provider id
`ananta_webcrawler_openai`; explicit profile selection remains possible even
for an otherwise non-web task, while automatic fallback remains tag-limited.

## Troubleshooting

- `webcrawler_unavailable`: verify URL, DNS/TLS, container/process health, and
  `/v1/models` reachability.
- `webcrawler_api_key_unavailable` or authentication failure: set the named
  environment variable in the Hub container; do not put the key in YAML.
- Draft/invalid/missing profile: validate or publish it in the external
  Webcrawler under its own governance, then refresh `/v1/models`.
- `webcrawler_startup_timeout`: inspect the external process/container and
  increase the bounded startup timeout only if startup is legitimately slow.
- Semantic mismatch: select a web-related task kind or explicitly select the
  provider and an allowlisted profile.
