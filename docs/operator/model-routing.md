# Model Routing Operator Guide

## Quick Start

Legacy mode still works:

```bash
DEFAULT_PROVIDER=lmstudio
DEFAULT_MODEL=auto
```

Profile mode is enabled by pointing the hub at a profiles file:

```bash
MODEL_PROFILES_PATH=config/models/examples/local-ollama-rtx3080.model_profiles.yaml
```

When both legacy defaults and `MODEL_PROFILES_PATH` are set, profile-based
routing has precedence and the legacy values remain fallback documentation.

## Local Ollama

Use local profiles for private source code and secret-bearing contexts:

```yaml
profiles:
  - profile_id: local_coder
    provider_id: ollama
    model: qwen2.5-coder:7b
    model_role: coder
    local: true
    cloud: false
    cloud_allowed: false
    block_secret_context: false
    supports_json: true
```

Ollama OpenAI-compatible chat endpoints are normalized to
`/v1/chat/completions` by `ModelInvocationService`.

## Local LM Studio

LM Studio is also local and serialized through an inference lock:

```yaml
profiles:
  - profile_id: local_chat
    provider_id: lmstudio
    model: auto
    model_role: chat
    local: true
    cloud: false
    cloud_allowed: false
```

## Hybrid Local and Cloud

Cloud profiles must opt in to cloud use and block secret contexts:

```yaml
profiles:
  - profile_id: cloud_reviewer
    provider_id: openrouter
    model: openai/gpt-4.1-mini
    model_role: reviewer
    local: false
    cloud: true
    cloud_allowed: true
    block_secret_context: true
    api_key_env: OPENROUTER_API_KEY
```

Security wins over model wishes. If the prompt or context looks secret-bearing,
cloud candidates are blocked and the resolver uses an allowed local fallback or
returns no profile.

## Debugging Routing

Useful places to inspect:

| Signal | Where |
|--------|-------|
| selected profile | `llm_call_profile`, task routing contracts |
| resolver source/rank | `model_resolver_source`, `model_resolver_rank` |
| blocked candidates | `model_blocked_candidates` |
| policy decisions | `model_policy_decisions` |
| prompt trace | prompt trace metadata, without API key values |
| config read model | `GET /config/model-routing/read-model` |

Provider health is part of routing. A provider marked unavailable is skipped by
explicit rules and capability matching until its TTL expires or it is reset.

## Recommended Standard Profiles

| Profile | Role | Suggested provider |
|---------|------|--------------------|
| `local_planner` | planner | Ollama or LM Studio |
| `local_coder` | coder | Ollama coder model |
| `local_tester` | tester | Ollama or LM Studio |
| `cloud_reviewer` | reviewer | OpenRouter/OpenAI-compatible, public context only |
| `cheap_summarizer` | summarizer | local small model or cheap cloud profile |
| `final_judge` | reviewer | strongest allowed profile for non-secret review |

## Operating Rules

- Keep private repos on local profiles unless policy explicitly allows cloud.
- Do not store API key values in profile files; use `api_key_env`.
- Treat `block_secret_context=true` as mandatory for every cloud profile.
- Prefer local fallbacks for all cloud profiles.
- Review `blocked_candidates` before assuming model routing is broken.
# Hybrid Local-First Routing

Ananta supports a generic local-to-cloud fallback chain without hardcoding
specific providers in runtime code. A recommended setup is:

```powershell
$env:MODEL_PROFILES_PATH="config/models/ananta.hybrid.local-openrouter.model_profiles.yaml"
$env:MODEL_ROUTING_PATH="config/models/ananta.hybrid.local-openrouter.model_routing.json"
$env:DEFAULT_PROVIDER="lmstudio"
$env:DEFAULT_MODEL="auto"
$env:LMSTUDIO_URL="http://localhost:1234/v1"
$env:OPENROUTER_API_KEY="<your OpenRouter key>"
```

The included `local_first_cheap` fallback group is ordered as:

1. `local_lmstudio_phi_json_worker` (`lmstudio`, `auto`, free, `prompt_json`)
2. `openrouter_gemma3_4b_cheap_json` (`google/gemma-3-4b-it`, very low cost, `both`)
3. `openrouter_qwen3_30b_a3b_stronger` (`qwen/qwen3-30b-a3b-instruct-2507`, stronger fallback, `both`)

Cloud profiles must set `cloud_allowed=true` and `block_secret_context=true`.
If prompt/context contains secret-like data, OpenRouter candidates are blocked
and local profiles remain the only eligible candidates.

`tool_calling_mode` values:

- `native_tools`: send OpenAI-compatible tool definitions to the provider.
- `prompt_json`: serialize the tool schema into the prompt and require a JSON
  object shaped like `{ "tool": "...", "args": { ... } }`.
- `both`: allow native tools, with prompt-json as a policy-visible capability.
- `none`: do not use tools for this profile.

Manual test plan:

1. Start LM Studio on `http://localhost:1234/v1`, set the env vars above, and run a small JSON/tool-selection step. Expected: `llm_call_profile[0].provider=lmstudio`, no OpenRouter cost.
2. Stop LM Studio and repeat. Expected: a timeout/connection error entry followed by Gemma in `llm_call_profile`.
3. Mock Gemma to return invalid JSON for a JSON-schema call. Expected: fallback decision with `trigger=invalid_json_response` and Qwen as the next attempt.
4. Add `OPENAI_API_KEY=sk-test...` or another secret-like value to context. Expected: Gemma/Qwen appear under `blocked_candidates`; no cloud call is made.
5. In the Visual Process Editor, set graph routing to `local_first_cheap` and run Dry-Run. Expected: each step shows `candidate_chain`, selected profile, blocked candidates, and estimated cost.
6. Start a workflow and inspect status/events. Expected: step metadata includes `selected_model_profile_id`, `fallback_attempts`, and any `llm_call_profile` emitted by the execution path.
