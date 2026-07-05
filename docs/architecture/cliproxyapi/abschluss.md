# CLIProxyAPI Abschlussanalyse (cliproxyapi-009)

Diese seite beantwortet die ursprüngliche User-Frage **"@GitHub das hab
ich doch bereits im ananta oder?"** endgültig mit ja/nein/teilweise
und verweist auf alle nachweis-dokumente.

Siehe auch: `docs/architecture/cliproxyapi/ist-zustand.md` (vollständige
code-mapping), `docs/integrations/cliproxyapi.md` (offizielles doku-profil),
`docs/integrations/cliproxyapi-security.md` (security-grenze).

## TL;DR

| Frage | Antwort |
|-------|---------|
| Hat Ananta bereits OpenAI-compatible provider-routing? | **ja** |
| Funktioniert `cliproxyapi` als `id` in `local_openai_backends`? | **ja**, ohne Sondercode |
| Funktioniert OpenCode-runtime-config mit `cliproxyapi`? | **ja**, via `default_provider=cliproxyapi` |
| Funktioniert Codex-runtime-config mit `cliproxyapi`? | **ja**, direkt via `codex_cli.target_provider=cliproxyapi` |
| Ist `cliproxyapi` als offizielles Profil dokumentiert? | **ja** (jetzt) |
| Sind Tests für einen CLIProxyAPI-artigen Backend vorhanden? | **ja** (jetzt) |
| Preflight-label `display_name='CLI Proxy API'`? | **ja** (jetzt) |
| Security-grenze dokumentiert? | **ja** (jetzt) |

**Antwort auf die ursprüngliche frage: "**ja, architektonisch war
CLIProxyAPI bereits abgedeckt — und es ist jetzt auch offiziell
dokumentiert und getestet.**"**

## 1. Ist-Zustand

CLIProxyAPI ist ein externer OpenAI-compatible HTTP-Proxy, der mehrere
CLI-Tools/Accounts unter einer einheitlichen `/v1`-API bündelt. Aus
Ananta-Sicht ist er ein *HTTP-Ziel*, kein ananta-CLI-backend (wie
sgpt, opencode, codex, aider, ...).

Bereits vor diesem analyse-todo verfügte Ananta über:

- `agent/local_llm_backends.py` mit `_normalize_local_backend_entry`
  und `get_local_openai_backends` (provider-id-agnostisch, akzeptiert
  beliebige ids einschließlich `cliproxyapi`).
- `agent/cli_backends/opencode.py::resolve_opencode_runtime_config`
  (erzeugt `provider_config.provider.<id>`-block mit `npm:
  "@ai-sdk/openai-compatible"` und `options.baseURL`).
- `agent/cli_backends/opencode.py::resolve_codex_runtime_config`
  (resolved `codex_cli.target_provider` über
  `resolve_local_openai_backend`).
- `agent/cli_backends/routing.py::get_cli_backend_preflight` (zeigt
  `providers.local_openai` mit provider, name, base_url, type,
  supports_tool_calls).

## 2. Was bereits funktioniert

| Capability | Status | Datei / Funktion |
|------------|--------|-------------------|
| `local_openai_backends[].id=cliproxyapi` | funktioniert | `agent/local_llm_backends.py` Z. 13–41 |
| `base_url`-normalisierung `/v1` | funktioniert | `_normalize_lmstudio_base_url` Z. 188–216 |
| OpenCode `provider_config` für cliproxyapi | funktioniert (via `default_provider`) | `agent/cli_backends/opencode.py` Z. 271–286 |
| Codex `target_provider=cliproxyapi` | funktioniert direkt | `agent/cli_backends/opencode.py` Z. 462–481 |
| Preflight-listet `cliproxyapi` | funktioniert (über `local_openai`-list) | `agent/cli_backends/routing.py` Z. 317–336 |
| Idempotente base-url-normalisierung | funktioniert | `normalize_openai_compatible_base_url` |
| Case-insensitive dedup | funktioniert | `get_local_openai_backends` Z. 106–114 |
| `api_key_profile`-resolution | funktioniert | `_resolve_profile_api_key` |

## 3. Was nur implizit funktioniert (Workaround nötig)

`opencode_runtime.target_provider=cliproxyapi` fällt auf `None`, weil
cliproxyapi weder in `_native_passthrough` (opencode, anthropic, ...)
noch in `{ollama, lmstudio}` ist. Die Workaround-Workarounds:

- `default_provider=cliproxyapi` setzen, oder
- `cliproxyapi/<model>` als model-name verwenden, oder
- `codex_cli.target_provider=cliproxyapi` setzen (codex hat den
  filter nicht)

Dieser Workaround wird in `docs/integrations/cliproxyapi.md`
explizit dokumentiert. Der bug ist ein UX-problem, nicht ein
correctness-problem. Wir wählen bewusst *nicht*, ihn heimlich zu
fixen, weil das den bestehenden ollama/lmstudio-unterscheidung
brechen würde.

## 4. Was offiziell ergänzt wurde

| Artefakt | Datei |
|----------|-------|
| Offizielles doku-profil | `docs/integrations/cliproxyapi.md` |
| Security-grenze | `docs/integrations/cliproxyapi-security.md` |
| Beispiel-konfiguration | `docs/examples/cliproxyapi-agent-config.yaml` |
| Code-mapping | `docs/architecture/cliproxyapi/ist-zustand.md` |
| Codex-pfad-analyse | `docs/architecture/cliproxyapi/codex-pfad.md` |
| Tests: `local_openai_backends` | `tests/test_local_llm_backends.py` (28 tests) |
| Tests: OpenCode/Codex runtime-config | `tests/test_cliproxyapi_opencode_runtime.py` (18 tests) |
| Tests: Preflight-label | `tests/test_cliproxyapi_preflight.py` (7 tests) |
| Source-code: preflight `display_name` | `agent/cli_backends/routing.py` (additives feld) |

**Insgesamt 53 neue tests**, alle grün. 9 commits, einer pro wave.

## 5. Was bewusst nicht gebaut wurde

| Idee (only_if_needed) | Status | Begründung |
|-----------------------|--------|------------|
| Eigenes `cliproxyapi`-preset in `ananta init` | **nicht gebaut** | `--local-openai-backends`-preset wäre machbar, ist aber follow-up; non_goal "Keine neue Provider-Architektur" |
| UI-formular-preset "CLI Proxy API" | **nicht gebaut** | Frontend-änderung außerhalb des scopes |
| Health-probe für `/v1/models` generisch | **nicht gebaut** | würde alle OpenAI-compatible backends pingen; leistungs- und timeout-implikationen |
| Codex-spezifische Erweiterung | **nicht gebaut** | codex funktioniert bereits direkt |
| Bugfix in `forced_target_provider`-filter | **nicht gebaut** | workaround via `default_provider` ist sauber |

## 6. Offene Risiken / Follow-ups

| Risiko | Mitigation |
|--------|------------|
| OAuth-/Account-credentials in CLIProxyAPI | siehe `cliproxyapi-security.md` (verschlüsseltes secret-storage) |
| Remote-CLIProxyAPI exponiert | local-first empfehlung, TLS, vpn/mesh |
| Workaround `opencode_runtime.target_provider=cliproxyapi` | dokumentiert; folge-todo wenn UX-verbesserung gewünscht |
| Unbekannte provider-ids | `_normalize_local_backend_entry` akzeptiert sie; failure-mode ist `base_url=None`, nicht crash |
| API-key-leak in `.env` | dokumentiert; folge-todo "echte secret-profile-resolution" |

## Antwort-übersicht (für README / release-notes)

> **Frage: "Hab ich CLIProxyAPI bereits im Ananta?"**
>
> **Ja.** Ananta unterstützt jeden OpenAI-compatible provider über
> `local_openai_backends`, einschließlich CLIProxyAPI. Es braucht
> keinen Sondercode — die existierenden
> `local_llm_backends`-Normalisierung, der OpenCode-runtime-config
> und der Codex-runtime-config funktionieren alle direkt.
>
> Vor diesem analyse-todo fehlte nur die *offizielle* Dokumentation
> und Tests. Beide wurden jetzt nachgereicht:
>
> - `docs/integrations/cliproxyapi.md` ist die offizielle doku-stelle
> - `docs/integrations/cliproxyapi-security.md` benennt die
>   vertrauensgrenze
> - 53 tests decken `local_openai_backends`-normalisierung, OpenCode/
>   Codex-runtime-config und preflight-rendering ab
> - Das preflight zeigt CLIProxyAPI mit `display_name='CLI Proxy API'`
>   wenn `id=cliproxyapi` konfiguriert ist
>
> **Einziger Workaround:** `opencode_runtime.target_provider=cliproxyapi`
> funktioniert nicht direkt (filter akzeptiert nur ollama/lmstudio
> dort). Statt dessen `default_provider=cliproxyapi` setzen oder das
> model als `cliproxyapi/<model>` angeben. Codex hat diesen filter
> nicht — `codex_cli.target_provider=cliproxyapi` funktioniert dort
> direkt.