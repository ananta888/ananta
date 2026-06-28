# Hybrid LM Studio + OpenRouter Routing

Use this runbook when you want cheap local work first, then paid fallback only
when policy allows it.

Configuration:

```powershell
$env:MODEL_PROFILES_PATH="config/models/ananta.hybrid.local-openrouter.model_profiles.yaml"
$env:MODEL_ROUTING_PATH="config/models/ananta.hybrid.local-openrouter.model_routing.json"
$env:LMSTUDIO_URL="http://localhost:1234/v1"
$env:OPENROUTER_API_KEY="<set only in the runtime environment>"
```

Expected chain:

`local_lmstudio_phi_json_worker -> openrouter_gemma3_4b_cheap_json -> openrouter_qwen3_30b_a3b_stronger`

No API key is stored in graph JSON, model profiles, prompt traces, or frontend
read-models. The frontend only receives `api_key_configured=true/false`.

Failure diagnostics:

- Missing API key: OpenRouter request fails cleanly while LM Studio remains usable.
- LM Studio unavailable: invocation records a failed local attempt, marks the provider unhealthy, then tries the next eligible profile.
- Profile not found: Visual Process validation emits `model_profile_missing`.
- Policy blocked: cloud candidates are listed under `blocked_candidates`, and no stronger cloud fallback bypasses the block.
