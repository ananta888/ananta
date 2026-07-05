# CLIProxyAPI Codex-Pfad-Analyse (cliproxyapi-006)

Diese analyse beantwortet die vier fragen aus
`todos/cliproxyapi-ist-zustand-analyse.todo.json` für den Codex-pfad
explizit.

## Quellen

- `agent/cli_backends/opencode.py` (Z. 449–525): `resolve_codex_runtime_config`
- `agent/local_llm_backends.py` (Z. 117–136): `resolve_local_openai_backend`
- `agent/config.py`: Settings (`codex_cli` block nicht-Teil der Settings, kommt aus `agent_cfg`)

## Frage 1: Kann Codex aktuell eine beliebige local_openai_backend Provider-ID nutzen?

**Antwort: ja, ohne Einschränkung.**

Beweis (`resolve_codex_runtime_config`, Z. 462–465):

```python
target_provider = str(codex_cfg.get("target_provider") or "").strip().lower() or None
...
local_target = resolve_local_openai_backend(
    target_provider,
    agent_cfg=agent_cfg,
    provider_urls=provider_urls,
) if target_provider else None
```

Es gibt **kein** filter wie in `resolve_opencode_runtime_config` (Z.
170–174: `if forced_target_provider not in {"ollama", "lmstudio"}: ...
None`). Beliebige provider-ids werden direkt an
`resolve_local_openai_backend` weitergereicht.

## Frage 2: Wird CLIProxyAPI base_url für Codex sauber aufgelöst?

**Antwort: ja, via local_openai_backends-Lookup.**

Beweis (Z. 470–472):

```python
elif local_target and local_target.get("base_url"):
    base_url = _normalize_openai_base_url(local_target.get("base_url"))
    base_url_source = f"codex_cli.target_provider:{local_target['provider']}"
```

Wenn `codex_cli.target_provider=cliproxyapi` und in
`local_openai_backends` ein eintrag mit `id=cliproxyapi` und
`base_url=http://localhost:8317/v1` existiert, dann ist
`base_url='http://localhost:8317/v1'` (oder die normalisierte form).

Die priorisierung der base-url-resolution (Z. 467–481) ist:

1. `codex_cli.base_url` (explizite override)
2. `local_target.base_url` (der lokale openai-backend-lookup)
3. `lmstudio_url` (wenn `prefer_lmstudio=true`)
4. Sonst die `default_provider`-base-url

Für CLIProxyAPI greift pfad 2 (das ist der gewünschte).

## Frage 3: Sind API-Key und Model-ID für Codex anders zu behandeln als für OpenCode?

**Antwort: ähnlich, mit kleineren unterschieden.**

API-key-resolution (Z. 483–504):

```python
api_key = str(codex_cfg.get("api_key") or "").strip() or None
api_key_source = "codex_cli.api_key" if api_key else None
if not api_key:
    api_key = _resolve_profile_api_key(codex_cfg.get("api_key_profile"))
    if api_key:
        api_key_source = "codex_cli.api_key_profile"
if not api_key and local_target:
    api_key = str(local_target.get("api_key") or "").strip() or None
    if api_key:
        api_key_source = f"local_openai.{local_target['provider']}"
    elif local_target.get("api_key_profile"):
        api_key = _resolve_profile_api_key(local_target.get("api_key_profile"))
        if api_key:
            api_key_source = f"local_openai.{local_target['provider']}.api_key_profile"
is_local = _is_probably_local_base_url(base_url)
if not api_key and is_local:
    api_key = "***"
    api_key_source = "local_dummy"
if not api_key:
    api_key = os.environ.get("OPENAI_API_KEY") or settings.openai_api_key
    if api_key:
        api_key_source = "openai_api_key"
```

Reihenfolge der key-resolution:

1. `codex_cli.api_key` (explizit)
2. `codex_cli.api_key_profile` (profil)
3. `local_target.api_key` (vom `cliproxyapi`-eintrag)
4. `local_target.api_key_profile` (profil aus dem eintrag)
5. lokal-dummy `"***"` (wenn base-url lokal wirkt)
6. `OPENAI_API_KEY` env-variable
7. `settings.openai_api_key`

Das ist **detaillierter** als der OpenCode-pfad. Insbesondere
`local_dummy "***"` für lokale base-urls ist codex-spezifisch und
verhindert, dass codex ohne api-key gegen einen lokalen OpenAI-server
fehlschlägt.

Model-ID: identisch zu opencode — `target_model` wird aus
`codex_cli.target_model` oder `default_model` gelesen (siehe
`_split_cli_model_identifier` in opencode.py Z. 53–63).

## Frage 4: Ist Codex hier wirklich Ziel-CLI oder nur Backend-Name im Routing?

**Antwort: beides.**

- Codex ist *auch* ein CLI-backend (im `SUPPORTED_CLI_BACKENDS`-set,
  `agent/cli_backends/routing.py` Z. 28) — das ist der
  codex-CLI-binary.
- Aber `resolve_codex_runtime_config` ist nicht der codex-CLI-runtime.
  Es ist die runtime-config für *welches* LLM codex-CLI nutzen soll.

Konkret: wenn du `codex-cli` als binary benutzt, wird
`resolve_codex_runtime_config` aufgerufen um zu entscheiden **welche
LLM** codex-CLI anspricht. CLIProxyAPI ist hier ein
LLM-ziel, kein CLI-binary.

Die zwei rolle von "codex" sind sauber getrennt:

- codex-CLI: ein binary, ausgeführt via `run_codex_command` (Z. 528 ff.)
- codex-runtime-config: ein konfig-block, gelesen via
  `resolve_codex_runtime_config`, der dem codex-CLI-binary sagt
  *welche* base-url und welcher model zu nutzen ist.

Für CLIProxyAPI heißt das: du kannst sowohl codex-CLI gegen
CLIProxyAPI laufen lassen (via `codex_cli.target_provider: cliproxyapi`)
als auch den generischen OpenAI-compatible pfad für andere
consumers.

## Fazit

Codex-pfad für CLIProxyAPI: **funktioniert bereits**, keine
code-änderung notwendig. Die analysen aus cliproxyapi-001 sind hier
spezifisch bestätigt:

- base_url-resolution über `resolve_local_openai_backend`
- api-key-resolution über die 7-stufige prioritätsliste
- model-id-resolution identisch zu opencode

## Falls follow-up nötig

Wenn zukünftig ein codex-spezifisches feature nötig wird (z.B.
model-allowlist, rate-limiting, retry-policy), gehört das in
einen separaten follow-up-todo, nicht in cliproxyapi-006. Das
verhindert vermischung mit dem opencode-pfad.