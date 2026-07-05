# Codex-CLI Worker-Agent in Ananta (CCA-001)

Diese Datei ist der **Ist-Zustand** der Codex-Integration in Ananta.
Sie verhindert Doppelimplementierung und dient als Grundlage für
CCA-002 (Account-Login-Modus).

## TL;DR

Codex ist in Ananta **bereits vollständig integriert** als
Worker-Agent-Backend mit der executor_kind `openai_codex_cli`.
Was fehlt ist ausschließlich der **Account-Login-Modus** (chatgpt_login
ohne API-Key-Zwang). Die bestehende API-Key-Pfad bleibt unverändert
nutzbar.

## Bestehende Codex-Codepfade

### Runtime: `run_codex_command`

Datei: `agent/cli_backends/opencode.py:528–587`

```python
def run_codex_command(prompt, model=None, timeout=60) -> tuple[int, str, str]:
    # 1. Budget-Check (check_prompt_budget)
    # 2. codex_bin = settings.codex_path or "codex"
    # 3. shutil.which(codex_bin) — fail-closed wenn binary fehlt
    # 4. args = [codex_resolved, "exec", "--skip-git-repo-check"]
    # 5. Optional: --model aus model oder settings.codex_default_model
    # 6. _acquire_backend_permit("codex", timeout=timeout) — semaphore
    # 7. env = os.environ.copy() + OPENAI_BASE_URL + OPENAI_API_BASE + OPENAI_API_KEY
    # 8. subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
    # 9. Returns (returncode, stdout, stderr)
```

**Heutiges Verhalten**: ohne `OPENAI_API_KEY` (wenn `is_local` False) wird
fail-closed abgebrochen mit `"Codex runtime target requires API key
for remote endpoint"` (Z. 558–559). Genau hier muss CCA-002 ansetzen.

### Settings: `codex_path` + `codex_default_model`

Datei: `agent/config.py:253–254`

```python
codex_path: str = Field(default="codex", validation_alias="CODEX_PATH")
codex_default_model: Optional[str] = Field(default="gpt-5-codex",
                                          validation_alias="CODEX_DEFAULT_MODEL")
```

Diese Settings existieren und sind nutzbar. `codex_auth_mode` und
`codex_require_api_key` müssen ergänzt werden.

### Config-Block: `codex_cli`

Datei: `agent/config_defaults.py:389–393`

```python
"codex_cli": {
    "base_url": None,
    "api_key_profile": None,
    "prefer_lmstudio": True,
},
```

Drei Felder heute. `auth_mode` (api_key|chatgpt_login), `api_key_required`
(bool) und `login_status_command` (str, optional) müssen ergänzt
werden.

### Runtime-Config: `resolve_codex_runtime_config`

Datei: `agent/cli_backends/opencode.py:449–525`

Liest `codex_cli.base_url`, `codex_cli.api_key_profile`,
`codex_cli.target_provider`. Liefert:
- `target_provider`: aufgelöster provider (z.B. "cliproxyapi")
- `base_url`, `base_url_source`
- `api_key`, `api_key_source` (inkl. `"local_dummy"` für local-backends)
- `target_kind`: `local_openai` / `remote_openai_compatible` /
  `remote_ananta_hub`
- `diagnostics`: list[str] mit fehler-hinweisen

Diese Funktion bleibt unverändert. CCA-002 erweitert sie um
`auth_mode`-aware logic.

### Backend-Registry

- `agent/cli_backends/routing.py:28` `SUPPORTED_CLI_BACKENDS`
  enthält `"codex"`.
- `agent/cli_backends/routing.py:32,42,64` `CLI_BACKEND_INSTALL_HINTS`,
  `CLI_BACKEND_VERIFY_COMMANDS`, `CLI_BACKEND_CAPABILITIES` haben
  codex-eintraege.
- `agent/cli_backends/semaphore.py:14`
  `_DEFAULT_BACKEND_PARALLEL_LIMITS` hat `"codex": 4`.

### Executor-Kind-Set

Drei dateien definieren `{"ananta_worker", "opencode", "openai_codex_cli", "custom"}`:

- `agent/services/worker_contract_service.py:171`
- `agent/services/worker_todo_planner_service.py:43`
- `agent/routes/config/shared.py:291`

`openai_codex_cli` ist die offizielle Ananta-id für codex.

### Provider-Contract

Datei: `agent/backend_provider_contracts.py:55–64`

```python
{
    "provider": "codex_cli",
    "provider_type": "cli_backend",
    "location": "local",
    "transport": {"protocol": "process", "api_shape": "task_scoped_cli"},
    "capabilities": {"chat": True, "tools": True,
                     "dynamic_models": False,
                     "file_access": "workspace_scoped"},
    "routing": {"eligible_for_inference": False,
                "eligible_for_execution": True,
                "remote_hops": 0},
    "governance": {"trust_level": "local_workspace",
                   "requires_remote_hub_policy": False,
                   "audit_required": True},
    "health": {"preflight": "cli_backend_preflight",
               "failure_mode": "execution_backend_unavailable"},
},
```

`codex_cli` ist als CLI-Backend klassifiziert (`provider_type`,
`transport.protocol=process`). Dies bestätigt, dass codex ein
**Worker-Agent-Backend** ist, kein OpenAI-kompatibler
ChatCompletion-Provider.

### Preflight

Datei: `agent/cli_backends/routing.py:382–397`

`get_cli_backend_preflight()` liefert für codex:
- `configured: bool`
- `base_url: str|None`
- `target_provider: str|None`
- `api_key_configured: bool`
- `api_key_source: str|None`
- `host_kind`: "loopback" | "private_network" | "remote" | "docker_host"
- `is_local: bool`
- `target_kind`
- `diagnostics: list[str]`

Diese Struktur bleibt. CCA-002 erweitert sie um:
- `auth_mode: "api_key" | "chatgpt_login"`
- `auth_status: "ready" | "not_logged_in" | "unknown"`
- `login_command: str|None` (z.B. `"codex login"` für not_logged_in)

### Config-API

Datei: `agent/routes/config/read_models.py:55`

`POST /config` mit `codex_cli` als editierbares objekt ist
bereits dokumentiert. Neue felder wie `auth_mode` werden hier
automatisch sichtbar.

### Tests

- `tests/test_codex_cli_backend.py` — `run_codex_command` mit mock
- `tests/test_codex_cli_backend_preflight.py` — preflight + runtime

Beide dateien sind gesplittet (siehe kommentar in
`test_codex_cli_backend_preflight.py:6` "Split from
test_codex_cli_backend.py to keep source files below 1000 lines.")

CCA-002 erweitert beide dateien um chatgpt_login-tests.

### Shell-Disziplin

5 stellen rufen `subprocess.run` ohne `shell=True`:

- `agent/cli_backends/opencode.py:429` (opencode)
- `agent/cli_backends/opencode.py:571` (codex)
- `agent/cli_backends/opencode.py:607` (aider)
- `agent/cli_backends/opencode.py:640` (mistral_code)
- `agent/cli_backends/sgpt.py:140`

Alle nutzen args-listen, `capture_output=True`, `text=True`,
`env=env`, `timeout=timeout`. Das ist COMMON-001-Disziplin.

### Codebase-Suche-Ergebnis für codex

| Datei | Symbol | Rolle |
|-------|--------|-------|
| `agent/cli_backends/opencode.py:528` | `run_codex_command` | runtime-execution |
| `agent/cli_backends/opencode.py:449` | `resolve_codex_runtime_config` | config-resolution |
| `agent/cli_backends/opencode.py:42` | `_build_codex_runtime_diagnostics` | preflight-diagnostics |
| `agent/cli_backends/routing.py:28` | `SUPPORTED_CLI_BACKENDS` | backend-registry |
| `agent/cli_backends/routing.py:64` | `CLI_BACKEND_CAPABILITIES["codex"]` | capability-config |
| `agent/cli_backends/routing.py:136` | `_resolve_backend_binary("codex")` | binary-resolution |
| `agent/cli_backends/routing.py:152` | `_configured_backend_command("codex")` | command-resolution |
| `agent/cli_backends/routing.py:193–207` | `get_cli_backend_runtime_status` | preflight-build |
| `agent/cli_backends/routing.py:382–397` | `get_cli_backend_preflight` | preflight-response |
| `agent/cli_backends/semaphore.py:14` | `_DEFAULT_BACKEND_PARALLEL_LIMITS` | concurrency-limits |
| `agent/cli_backends/sgpt.py:362` | `run_codex_command(...)` (im sgpt-route) | call-site |
| `agent/cli_backends/sgpt.py:43,46` | imports | dependency-injection |
| `agent/backend_provider_contracts.py:55` | `codex_cli` contract | contract-declaration |
| `agent/config.py:253–254` | `codex_path`, `codex_default_model` | settings |
| `agent/config_defaults.py:389` | `codex_cli` config-block | default-config |
| `agent/providers/worker_execution.py:12,51` | `openai_codex_cli` provider | routing |
| `agent/routes/config/read_models.py:55` | `codex_cli` schema-eintrag | api-docs |
| `agent/routes/config/read_models.py:121–137` | `codex_cli` schema-builder | api-serialization |
| `agent/routes/sgpt.py:443,452` | `codex_runtime` integration | api-route |
| `agent/services/strategy_mode_service.py:57` | `codex_cli_like` strategy_mode | strategy |
| `agent/services/worker_contract_service.py:171` | executor_kind-set | worker-contract |
| `agent/services/worker_selection_policy_service.py:200` | `_EXPENSIVE_WORKER_PREFIXES` | selection-policy |
| `agent/services/worker_todo_planner_service.py:43` | executor_kind-set | planning |
| `agent/routes/config/shared.py:291` | executor_kind-set | config-shared |
| `frontend-angular/.../codehug.models.ts:173` | `ChCliBackend` | angular-types |
| `frontend-angular/.../codehug.models.ts:659` | `preferredBackend` | angular-types |
| `tests/test_codex_cli_backend.py` | 4 tests | runtime-tests |
| `tests/test_codex_cli_backend_preflight.py` | 5 tests | preflight-tests |

## Was CCA-002 ergänzen muss

CCA-002 zielt auf:

1. **`agent/config.py:253–254`**: `codex_auth_mode: str = "api_key"`
   und `codex_require_api_key: bool = True` settings hinzufügen.
2. **`agent/config_defaults.py:389`**: `codex_cli.auth_mode` und
   `codex_cli.api_key_required` hinzufügen.
3. **`agent/cli_backends/opencode.py:528`** `run_codex_command`:
   Z. 558–559 anpassen so dass `api_key` check umgangen wird wenn
   `auth_mode=chatgpt_login` und `is_local` irrelevant ist.
4. **`agent/cli_backends/routing.py:382`**: preflight-response um
   `auth_mode`, `auth_status`, `login_command` erweitern.
5. **`agent/cli_backends/opencode.py:42`** `_build_codex_runtime_diagnostics`:
   für `auth_status`-Ermittlung.
6. **`tests/test_codex_cli_backend.py`** und
   `tests/test_codex_cli_backend_preflight.py`:
   Tests für chatgpt_login-Modus hinzufügen.

## Was CCA-003 ergänzen muss

CCA-003 zielt auf:

1. `frontend-angular`: UI-Karte für codex mit auth_mode-toggle.
   `ChCliBackend` ist bereits vorbereitet, `auth_mode` muss ergänzt
   werden.
2. `agent/routes/sgpt.py:443`: `auth_mode`, `auth_status`,
   `login_command` in `codex_runtime_target` response ergänzen.

## Sicherheits-relevante punkte

- **Keine tokens lesen oder speichern**: `auth_status` wird über
  offizielle codex-cli subcommands ermittelt, nicht durch direktes
  Filesystem-Lesen.
- **OPENAI_API_KEY umgehen wenn auth_mode=chatgpt_login**:
  Z. 564–565 in `run_codex_command` muss `env["OPENAI_API_KEY"]`
  nicht gesetzt werden wenn `auth_mode=chatgpt_login` aktiv ist.
  Codex-cli nutzt dann seine eigene `~/.codex/auth.json`.
- **Kein web-scraping**: ChatGPT-Login-status wird via
  `codex login status` o.ä. abgefragt, nicht via http-anfrage an
  chat.openai.com.

## Migration-rule

Bestehende `OPENAI_API_KEY`-Nutzung bleibt rückwärtskompatibel:

- `auth_mode=api_key` (default): aktuelles Verhalten unverändert
- `auth_mode=chatgpt_login`: api_key nicht erforderlich für
  remote-URL, codex nutzt `~/.codex/auth.json`

Nutzer migrieren freiwillig via `codex_cli.auth_mode=chatgpt_login`.
Bestehende config-files ohne `auth_mode`-Feld bleiben im
api_key-Modus.