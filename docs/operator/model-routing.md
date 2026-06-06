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
