# Lokale LLM-Strategie und CLI-Backends

Dieses Dokument konsolidiert den bevorzugten Betriebsweg fuer lokale Modellruntimes und optionale CLI-Backends.

## Zielbild

- `lmstudio` ist der bevorzugte lokale Standard-Provider.
- Cloud-Provider wie `openai`, `codex` oder `anthropic` bleiben moeglich, sollen aber explizit konfiguriert sein.
- CLI-Backends (`sgpt`, `codex`, `opencode`, `aider`, `mistral_code`) werden getrennt von der LLM-Provider-Wahl betrachtet.
- Das Codex-CLI-Backend kann entweder gegen OpenAI oder gegen eine lokale OpenAI-kompatible Runtime wie LM Studio laufen.

## Empfohlener Standardpfad

1. LM Studio lokal starten.
2. In LM Studio mindestens ein Chat-Modell laden.
3. In Ananta `default_provider=lmstudio` und eine erreichbare `lmstudio_url` setzen.
4. Fuer `codex_cli` entweder explizit `base_url` setzen oder `prefer_lmstudio=true` belassen.
5. Optional CLI-Binaries installieren, wenn hybride oder code-nahe Workflows benoetigt werden.

## Wichtige Konfiguration

Relevante Schluessel liegen in `AGENT_CONFIG` bzw. den Runtime-Settings:

- `default_provider`
- `default_model`
- `lmstudio_url`
- `lmstudio_api_mode`
- `sgpt_execution_backend`
- `codex_cli.base_url`
- `codex_cli.api_key_profile`
- `codex_cli.prefer_lmstudio`
- `llm_api_key_profiles`

Typische lokale LM-Studio-URL:

```text
http://127.0.0.1:1234/v1
```

In Docker-/WSL-Setups ist haeufig auch dieser Pfad relevant:

```text
http://host.docker.internal:1234/v1
```

## CLI-Backends installieren

Minimal fuer lokale Standardnutzung:

```bash
python -m pip install shell-gpt
```

Optionale Code-/Agenten-Backends:

```bash
npm i -g @openai/codex
npm i -g opencode-ai
python -m pip install aider-chat
npm i -g mistral-code
```

## Codex-CLI-Runtime

Der Backend-Pfad `codex` ist getrennt vom API-Provider `codex`.

Prioritaet fuer das effektive Codex-CLI-Ziel:

1. `codex_cli.base_url`
2. `lmstudio_url`, wenn `codex_cli.prefer_lmstudio=true`
3. Default-Provider/OpenAI-kompatible Runtime

API-Key-Prioritaet fuer Codex CLI:

1. `codex_cli.api_key`
2. `codex_cli.api_key_profile`
3. globaler `OPENAI_API_KEY`
4. lokaler Dummy-Key bei lokalem Ziel

Damit kann Codex CLI lokal gegen LM Studio laufen, ohne auf Cloud-Zugang angewiesen zu sein.

## Verifikation und Preflight

Fuer den operativen Check sind drei Endpunkte relevant:

- `GET /health`
- `GET /ready`
- `GET /api/sgpt/backends`

`/api/sgpt/backends` liefert:

- `supported_backends`: statische Faehigkeiten
- `runtime`: Laufzeitstatus und Health-Score der CLI-Backends
- `preflight`: installierte Binaries, Install-Hinweise und Runtime-Ziele fuer `lmstudio` und `codex`

Wichtige Preflight-Felder:

- `preflight.cli_backends.<name>.binary_available`
- `preflight.cli_backends.<name>.install_hint`
- `preflight.providers.lmstudio.base_url`
- `preflight.providers.lmstudio.host_kind`
- `preflight.providers.lmstudio.reachable`
- `preflight.providers.lmstudio.candidate_count`
- `preflight.providers.codex.base_url`
- `preflight.providers.codex.base_url_source`
- `preflight.providers.codex.api_key_configured`

## Host-Erkennung

Die Preflight-Ausgabe klassifiziert lokale Ziele derzeit als:

- `loopback`
- `docker_host`
- `private_network`
- `remote`
- `unknown`

Das hilft beim Einordnen von Fehlkonfigurationen, vor allem in Docker-/WSL-Umgebungen.

## Typische Fehlerbilder

### LM Studio ist konfiguriert, aber nicht erreichbar

Pruefen:

- lauscht LM Studio wirklich auf der erwarteten Adresse?
- ist ein Modell geladen?
- ist die Adresse aus Docker/WSL erreichbar?

Unter Windows/WSL siehe auch [DOCKER_WINDOWS.md](/mnt/c/Users/pst/IdeaProjects/ananta/docs/DOCKER_WINDOWS.md).

### Codex CLI hat kein sinnvolles Ziel

Pruefen:

- `codex_cli.base_url`
- `codex_cli.prefer_lmstudio`
- `lmstudio_url`
- `OPENAI_API_KEY` oder konfiguriertes API-Key-Profil

### Binary fehlt

`/api/sgpt/backends` zeigt pro Backend `binary_available=false` und einen passenden `install_hint`.

## Empfehlung fuer lokale Entwicklung

- Fuer allgemeine lokale Nutzung: `default_provider=lmstudio`
- Fuer Coding-Workflows: `sgpt_execution_backend=codex` oder `auto`
- Fuer Codex lokal ohne Cloud: `codex_cli.prefer_lmstudio=true`
- Vor Live-Tests immer `/api/sgpt/backends` und `/ready` pruefen
