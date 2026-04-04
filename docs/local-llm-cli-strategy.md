# Lokale LLM-Strategie und CLI-Backends

Dieses Dokument konsolidiert den bevorzugten Betriebsweg fuer lokale Modellruntimes und optionale CLI-Backends.

## Zielbild

- `lmstudio` ist der bevorzugte lokale Standard-Provider.
- Weitere lokale OpenAI-kompatible Runtimes koennen ueber `local_openai_backends` angebunden werden.
- Cloud-Provider wie `openai`, `codex` oder `anthropic` bleiben moeglich, sollen aber explizit konfiguriert sein.
- CLI-Backends (`sgpt`, `codex`, `opencode`, `aider`, `mistral_code`) werden getrennt von der LLM-Provider-Wahl betrachtet.
- Research-Backends wie `deerflow` werden getrennt von LLM-Providern und Coding-CLIs betrachtet.
- Das Codex-CLI-Backend kann entweder gegen OpenAI oder gegen eine lokale OpenAI-kompatible Runtime wie LM Studio laufen.

## Empfohlener Standardpfad

1. LM Studio lokal starten.
2. In LM Studio mindestens ein Chat-Modell laden.
3. In Ananta `default_provider=lmstudio` und eine erreichbare `lmstudio_url` setzen.
4. In dieser Windows-11-/WSL2-/Docker-Desktop-Umgebung wurde die Docker-seitig funktionierende Host-Route mit `http://172.18.96.1:1234/v1` verifiziert. Diese Adresse ist fuer Compose der bevorzugte Standard.
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
- `local_openai_backends`

Typische lokale LM-Studio-URL ausserhalb von Docker:

```text
http://127.0.0.1:1234/v1
```

In diesem Projekt-Setup unter Docker/WSL ist derzeit dieser Pfad der verifizierte Standard:

```text
http://172.18.96.1:1234/v1

`host.docker.internal:1234` kann weiterhin als Fallback funktionieren, ist in dieser Umgebung aber nicht der stabile Standardpfad.
```

Beispiel fuer einen zusaetzlichen lokalen OpenAI-kompatiblen Backend-Eintrag:

```json
{
  "local_openai_backends": [
    {
      "id": "vllm_local",
      "name": "vLLM Local",
      "base_url": "http://127.0.0.1:8010/v1/chat/completions",
      "models": ["qwen2.5-coder"],
      "supports_tool_calls": true,
      "api_key_profile": "local-dev"
    }
  ]
}
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
- `preflight.cli_backends.<name>.verify_command`
- `preflight.providers.lmstudio.base_url`
- `preflight.providers.lmstudio.host_kind`
- `preflight.providers.lmstudio.reachable`
- `preflight.providers.lmstudio.candidate_count`
- `preflight.providers.codex.base_url`
- `preflight.providers.codex.base_url_source`
- `preflight.providers.codex.api_key_configured`
- `preflight.providers.codex.target_kind`
- `preflight.providers.codex.target_provider_type`
- `preflight.providers.codex.remote_hub`
- `preflight.providers.codex.instance_id`
- `preflight.providers.codex.max_hops`
- `preflight.providers.codex.diagnostics`
- `preflight.providers.local_openai`

`GET /api/sgpt/backends` liefert zusaetzlich `routing_dimensions`, damit Inference-Default und Execution-Default explizit getrennt sichtbar sind.

Ergaenzend dazu fuehrt `setup_host_services.ps1` auf Windows jetzt einen lokalen Host-Preflight aus fuer:

- LM Studio `GET /v1/models`
- Agent-Health auf `5000/5001/5002`
- optionale CLI-Binaries `codex`, `opencode`, `aider`, `mistral-code`

Damit lassen sich Host-Probleme bereits vor dem Container- oder Frontend-Lauf eingrenzen.

## Benchmark-basierte Modellwahl

`POST /llm/generate` kann jetzt bei fehlender expliziter Provider-/Modellwahl die bestgeeignete verfuegbare Runtime aus den Benchmarkdaten waehlen.

Voraussetzungen:

- es gibt Benchmark-Eintraege fuer `task_kind`
- das Modell ist im aktuellen Provider-Katalog verfuegbar
- die Anfrage uebergibt nicht explizit `config.provider` oder `config.model`

Die Routing-Metadaten enthalten dann:

- `routing.task_kind`
- `routing.recommendation`
- `routing.effective.transport_provider`

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
- meldet `setup_host_services.ps1` einen erfolgreichen `candidate_count` fuer `/v1/models`?

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
