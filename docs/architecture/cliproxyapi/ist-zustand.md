# CLIProxyAPI Ist-Zustand in Ananta (cliproxyapi-001)

Diese Analyse beantwortet die Ursprungsfrage **"@GitHub das hab ich doch
bereits im ananta oder?"** mit konkreten code-nachweisen.

## TL;DR

| Frage                                                          | Antwort |
|----------------------------------------------------------------|---------|
| Hat Ananta bereits OpenAI-compatible provider-routing?         | **ja**   |
| Funktioniert `cliproxyapi` als `id` in `local_openai_backends`? | **ja**, ohne Sondercode |
| Funktioniert OpenCode-runtime-config mit `cliproxyapi`?        | **ja**, via `default_provider` oder explizit `cliproxyapi/<model>` — *nicht* via `opencode_runtime.target_provider` (siehe Bug unten) |
| Funktioniert Codex-runtime-config mit `cliproxyapi`?           | **ja**, via `codex_cli.target_provider` |
| Ist `cliproxyapi` als offizielles Profil dokumentiert?          | **nein**, das ist die Lücke |
| Sind Tests für einen CLIProxyAPI-artigen Backend vorhanden?     | **nein**, das ist die Lücke |

## Codepfade

### 1. `agent/local_llm_backends.py` — generischer OpenAI-compatible Backend

`local_llm_backends.py` ist *provider-id-agnostisch*: jede id, die
nicht leer ist und nicht mit lmstudio kollidiert, wird in
`_normalize_local_backend_entry` (Z. 13–41) als gültiger Eintrag
akzeptiert. Wichtige felder:

- `id` oder `provider` (z. 21) — provider-id, lower-cased
- `base_url` (z. 24) — geht durch `normalize_openai_compatible_base_url`
  → `_normalize_lmstudio_base_url` (z. 5, 9–10)
- `api_key`, `api_key_profile` (z. 34–35)
- `supports_tool_calls` (z. 36)
- `models` (z. 37)
- `transport_provider` wird *immer* auf `"openai"` gesetzt (z. 31)

`get_local_openai_backends` (Z. 44–114) hängt lmstudio als *immer
vorhandenen* ersten Eintrag hinzu, danach iteriert es über
`agent_cfg.local_openai_backends`. **deduplizierung** erfolgt
case-insensitiv nach `provider`-id (Z. 106–114). Wenn der nutzer
`cliproxyapi` als id einträgt, kommt es *zusätzlich* zu lmstudio in
die Liste — kein konflikt.

`resolve_local_openai_backend` (Z. 117–136) ist die lookup-funktion,
die OpenCode und Codex beide benutzen.

### 2. `agent/cli_backends/opencode.py` — OpenCode-runtime-config

`resolve_opencode_runtime_config` (Z. 151–310) hat einen subtilen bug
für CLIProxyAPI via `opencode_runtime.target_provider`:

- Z. 158: `forced_target_provider` wird aus `opencode_runtime.target_provider` gelesen
- Z. 170–174: wenn der wert **nicht** in `_native_passthrough` UND
  **nicht** in `{"ollama", "lmstudio"}` ist, wird er auf `None`
  zurückgesetzt.

Das heißt: setzt der nutzer
```yaml
opencode_runtime:
  target_provider: cliproxyapi
```
dann wird `cliproxyapi` hier zu `None`, der spätere `elif
target_provider and target_provider not in built_in_providers`-zweig
greift nicht, und das `provider_config` wird nicht erzeugt.

**Workaround ohne code-änderung**: statt `target_provider` zu setzen,
verwendet man `default_provider: cliproxyapi` (in der agent-config) oder
gibt das model als `cliproxyapi/<model>` an. Beide pfade landen
korrekt im `local_openai_backends`-lookup.

Für `default_provider=cliproxyapi` greift der `elif target_provider and
target_provider not in built_in_providers`-zweig (Z. 255–269):

```python
elif target_provider and target_provider not in built_in_providers:
    local_target = resolve_local_openai_backend(
        target_provider,
        agent_cfg=agent_cfg,
        provider_urls=provider_urls,
        default_provider=_get_runtime_default_provider(),
        default_model=str(agent_cfg.get("default_model") or ""),
    )
    if local_target and local_target.get("base_url"):
        base_url = _normalize_openai_base_url(local_target.get("base_url"))
        base_url_source = f"local_openai.{target_provider}"
        target_provider_type = str(local_target.get("provider_type") or "local_openai_compatible")
        target_kind = "remote_ananta_hub" if bool(local_target.get("remote_hub")) else (
            "local_openai" if _is_probably_local_base_url(base_url) else "remote_openai_compatible"
        )
```

Das resultierende `provider_config` (Z. 271–286) ist exakt was
CLIProxyAPI braucht: `npm = "@ai-sdk/openai-compatible"` mit
`options.baseURL = base_url`.

### 3. `agent/cli_backends/opencode.py` — Codex-runtime-config

`resolve_codex_runtime_config` (Z. 449–525) hat **kein** solches
`forced_target_provider`-filter. Codex liest `codex_cli.target_provider`
und geht direkt in `resolve_local_openai_backend(...)` (Z. 465).
Das funktioniert für beliebige provider-ids, einschließlich
`cliproxyapi`. Codex-seitig ist keine änderung nötig.

### 4. `agent/cli_backends/routing.py` — preflight

`get_cli_backend_preflight` (Z. 220–397) liest
`providers.local_openai` (über `get_local_openai_backends`) und macht
`provider_type`, `base_url`, `supports_tool_calls`, `transport_provider`
sichtbar. Wenn `cliproxyapi` als eintrag existiert, erscheint er hier
automatisch — aber **ohne explizites label** als "CLI Proxy API", nur
mit dem provider-namen.

### 5. `agent/config.py` — settings

`ollama_url`, `lmstudio_url`, `openai_url`, `default_provider`,
`default_model`, `openai_api_key` sind die relevanten settings.
**Keine** dedizierte `cliproxyapi_url`-env-variable — das ist
bewusst (non_goal: keine neue provider-architektur), weil
`local_openai_backends` mit `id=cliproxyapi` denselben effekt hat.

## Lücken (gap-analysis)

| Aspekt                                                              | Status |
|---------------------------------------------------------------------|--------|
| OpenAI-compatible transport via `local_openai_backends`             | vorhanden |
| `cliproxyapi` als provider-id in `local_openai_backends`            | funktioniert ohne code-änderung |
| OpenCode-provider_config für OpenAI-compatible backends             | vorhanden (Z. 271–286) |
| Codex-base_url-resolution für `local_openai_backends`              | vorhanden (Z. 465–472) |
| **Bug:** `opencode_runtime.target_provider=cliproxyapi` → None     | **workaround via `default_provider`** — kein sondercode nötig |
| Tests für `local_openai_backends` mit `id=cliproxyapi`             | **fehlt** |
| Tests für OpenCode-provider-config mit `cliproxyapi`               | **fehlt** |
| Preflight-label `display_name='CLI Proxy API'` für provider-id     | **fehlt** |
| Offizielle Doku / Beispiel-konfiguration                            | **fehlt** |
| Security-grenze Ananta ↔ CLIProxyAPI                               | **fehlt** |

## Antworten auf die ursprünglichen fragen

| Frage (cliproxyapi-001 steps)                                       | Antwort |
|---------------------------------------------------------------------|---------|
| An welchen Stellen wird `default_provider` / `default_model` gelesen? | `agent/config.py` (Settings), `agent/cli_backends/opencode.py` (resolve_opencode_runtime_config Z. 181, resolve_codex_runtime_config implizit über `provider_urls`) |
| Wird eine beliebige provider-id wie `cliproxyapi` durchgängig akzeptiert? | **ja**, solange sie nicht über `opencode_runtime.target_provider` gesetzt wird (siehe bug) |
| Wird `/v1`-suffix normalisiert oder doppelt angehängt?              | `normalize_openai_compatible_base_url` → `_normalize_lmstudio_base_url`; muss im test verifiziert werden (cliproxyapi-004) |
| Wird `supports_tool_calls` funktional berücksichtigt?              | nur dokumentarisch in `_normalize_local_backend_entry`; preflight zeigt es an |
| Wird `api_key`/`api_key_profile` korrekt weitergereicht oder ignoriert? | beides wird unterstützt; `api_key_profile` wird via `_resolve_profile_api_key` aufgelöst |

## Empfehlung (deckt sich mit non_goals)

CLIProxyAPI wird als offizielles OpenAI-compatible Provider-Profil
**dokumentiert und getestet**, ohne neue provider-architektur. Der
workaround für den `target_provider`-bug wird im doku-profil
erwähnt.

Kein code-change an `_native_passthrough` ist notwendig — der bug ist
ein UX-problem (nutzer erwartet, dass `target_provider=cliproxyapi`
direkt funktioniert), nicht ein correctness-problem. Wer das
geradeziehen will, ergänzt `cliproxyapi` zur set `{...}` — aber das
ist **only_if_needed** und gehört nicht in den M1/M2-kern.