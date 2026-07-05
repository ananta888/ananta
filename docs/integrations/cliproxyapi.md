# CLIProxyAPI Integration

CLIProxyAPI ist ein externer OpenAI-compatible HTTP-Proxy, der mehrere
CLI-Tools und Accounts unter einer einheitlichen `/v1`-API routet.
Für Ananta ist CLIProxyAPI **kein neues Backend** — es ist ein
OpenAI-compatible Provider-Ziel, das bereits über
`agent.local_llm_backends` / `local_openai_backends` angebunden wird.

Diese Seite ist die offizielle Doku-Stelle für die Integration. Sie
verweist auf das Ist-Zustand-mapping für die code-nachweise.

## Quickstart

### 1. CLIProxyAPI starten

CLIProxyAPI läuft typischerweise lokal auf `http://localhost:8317`.
Die tatsächliche URL hängt von deiner CLIProxyAPI-Installation ab
(Container, Port, Host). Konsultiere die CLIProxyAPI-Doku für
Startkommandos, OAuth-Logins und Modell-Discovery.

Wichtig: die CLI/OAuth-Logins liegen **in CLIProxyAPI**, nicht in
Ananta. Ananta sieht nur einen OpenAI-compatible HTTP-Endpunkt.

### 2. Ananta-YAML schreiben

```yaml
default_provider: cliproxyapi
default_model: codex/gpt-5.5-codex

local_openai_backends:
  - id: cliproxyapi
    name: CLI Proxy API
    base_url: http://localhost:8317/v1
    api_key: ***                       # siehe 'Secrets' unten
    supports_tool_calls: true
    models:
      - codex/gpt-5.5-codex
      - claude/sonnet
      - gemini/gemini-pro
```

Diese datei landet unter `config/ananta-agent.yaml` oder wird via
`AGENT_CONFIG` env-variable übergeben (siehe `docs/setup/ananta_init.md`).

### 3. Optional via env

```bash
export DEFAULT_PROVIDER=cliproxyapi
export DEFAULT_MODEL=codex/gpt-5.5-codex
```

Env-vars überschreiben nur die top-level `default_provider` /
`default_model`. Die `local_openai_backends`-liste selbst kommt aus
der YAML.

## Wie die integration funktioniert

```
+-----------------+         +--------------------+        +----------------+
|  Ananta/OpenCode|  HTTP   |  CLIProxyAPI        | intern | CLI-Tools /   |
|  / Codex /      |-------->|  (OpenAI-compatible |------->| Accounts       |
|  Ananta-Worker  |  /v1    |   proxy)            |        |                |
+-----------------+         +--------------------+        +----------------+
        ^                              ^
        |  base_url=http://localhost:  |
        |  8317/v1                     |
        |                              +-- /v1/models liefert Liste der verfuegbaren Modelle
        +-- Ananta spricht gegen /v1/chat/completions wie gegen OpenAI
```

- **Ananta-Worker** schickt HTTP-requests an die `base_url`.
- **OpenCode-runtime-config** erzeugt ein `provider_config.provider.<id>`-
  block mit `npm: "@ai-sdk/openai-compatible"` und `options.baseURL`
  (siehe `agent/cli_backends/opencode.py`).
- **Codex-runtime-config** resolved `codex_cli.target_provider` über
  `resolve_local_openai_backend(...)`.

Es gibt **keinen** Sondercode für CLIProxyAPI. Die integration nutzt
ausschließlich die OpenAI-compatible-Pfade die für LM Studio und
Ollama schon existieren.

## Wichtiger workaround für `opencode_runtime.target_provider`

Wenn du CLIProxyAPI als OpenCode-Backend benutzen willst, setze
**entweder**:

```yaml
default_provider: cliproxyapi      # globale default
```

oder das model explizit als `cliproxyapi/<model>`:

```yaml
default_model: cliproxyapi/gpt-5.5-codex
```

Setze **nicht** `opencode_runtime.target_provider: cliproxyapi` — der
runtime-config-resolver akzeptiert dort nur `ollama` oder `lmstudio`
(bekannter bug im opencode-runtime-config-pfad, siehe
`docs/architecture/cliproxyapi/ist-zustand.md` für details). Der
workaround via `default_provider` ist sauber und ohne code-änderung.

Für Codex gibt es diesen workaround **nicht** — `codex_cli.target_provider:
cliproxyapi` funktioniert direkt.

## Preflight / Discovery

`python -m agent.cli.preflight` (oder das entsprechende ananta-cli
kommando) zeigt CLIProxyAPI unter `providers.local_openai` mit allen
relevanten feldern an:

- `provider`: `cliproxyapi`
- `name`: `CLI Proxy API` (falls du `name:` gesetzt hast)
- `base_url`: deine konfigurierte URL
- `supports_tool_calls`: true/false
- `provider_type`: `local_openai_compatible`

Eine nicht-konfigurierte CLIProxyAPI erzeugt **keine** Fehlermeldung
im preflight — sie erscheint schlicht nicht.

Für eine runtime-discovery der modelle die CLIProxyAPI tatsächlich
anbietet, rufe `<base_url>/v1/models` direkt auf. Das preflight
selbst startet **keine** HTTP-probes.

## Troubleshooting

### WSL2 / Windows Host-IP

CLIProxyAPI läuft typischerweise auf dem Windows-host. Aus WSL2 ist
der host erreichbar als:

- `host.docker.internal` (wenn CLIProxyAPI in einem container läuft)
- die Windows-host-IP (oft `192.168.x.x` oder via `cat /etc/resolv.conf`
  → `nameserver`)

`localhost:8317` funktioniert **nicht** aus WSL2 heraus, wenn
CLIProxyAPI auf dem Windows-host läuft.

### Falsche `base_url`

Die `base_url` **muss** mit `/v1` enden, weil `normalize_openai_compatible_base_url`
den suffix normalisiert. Eine doppelte angabe (`/v1/v1`) wird
verhindert (siehe test cliproxyapi-004).

### `supports_tool_calls`

Wenn `supports_tool_calls` auf `false` steht, blockiert Ananta
tool-calls für diesen provider. CLIProxyAPI selbst bestimmt ob die
gerouteten modelle tool-calls unterstützen — wenn nicht, setze
`supports_tool_calls: false` oder lasse das feld weg.

### Authentifizierung

CLIProxyAPI verlangt je nach konfiguration einen api-key oder nicht.
Im lokalen betrieb ist der key oft `***` oder beliebig. Echte keys
kommen nur über env-vars oder ein secret-profile (`api_key_profile`),
niemals direkt in YAML die ins repo wandert.

## Secrets

CLIProxyAPI-authentifizierung gehört **nicht** in versionskontrolle.
Drei optionen:

1. **Lokal, kein key**: viele CLIProxyAPI-konfigurationen verlangen
   für lokale zugriffe keinen key. `api_key: ***` ist in diesem fall
   ein dummy-platzhalter und wird vom adapter nicht weitergegeben.

2. **Env-variable**: `export OPENAI_API_KEY=...` (oder ein
   dedizierter `*_API_KEY`). Ananta liest sie via
   `settings.openai_api_key` o.ä.

3. **Secret-profile**: `api_key_profile: my_cliproxy` (von einem
   externen secret-store aufgelöst). Siehe
   `_resolve_profile_api_key`.

Was du **nie** in `local_openai_backends[*].api_key` schreibst: echte
tokens, account-passwörter, OAuth-refresh-tokens. Solche inhalte
landen in env-vars, secret-stores oder vault-systemen.

## Was CLIProxyAPI nicht ist

CLIProxyAPI ist **kein** ananta-cli-backend (sgpt, opencode, codex,
aider, ...). Es ist ein externer HTTP-proxy. Der unterschied ist:

| Aspekt              | ananta-cli-backend                | CLIProxyAPI                       |
|---------------------|-----------------------------------|-----------------------------------|
| Aufgerufen via      | subprocess (CLI-binary)           | HTTP (`/v1/chat/completions`)     |
| Konfiguration       | `agent.cli_backends.<name>`       | `agent.local_llm_backends.<id>`   |
| Capability-tracking | CLI-spezifisch (subprocess, sandbox) | Standard OpenAI-compatible API    |
| Wo Authentifizierung lebt | nutzer-system / oauth-login     | CLIProxyAPI-konfiguration         |

Ananta routet HTTP-requests an CLIProxyAPI wie an jeden anderen
OpenAI-compatible provider. CLI-Tools werden innerhalb CLIProxyAPI
aufgerufen, nicht von Ananta.

## Cross-references

* `docs/architecture/cliproxyapi/ist-zustand.md` — code-mapping
* `docs/examples/cliproxyapi-agent-config.yaml` — beispiel-konfiguration
* `docs/codecompass-tools.md` — OpenAI-compatible tooling in Ananta