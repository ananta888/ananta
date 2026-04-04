# Container Networking Matrix

Diese Matrix konsolidiert die relevanten lokalen URL-/DNS-Pfade fuer Docker Compose, Host und WSL2.

## Zielbild

- Frontend bleibt ueber den Host erreichbar: `http://localhost:4200`
- Hub bleibt ueber den Host erreichbar: `http://localhost:5000`
- Worker bleiben ueber den Host erreichbar: `http://localhost:5001`, `http://localhost:5002`
- Container sprechen sich untereinander ueber Compose-DNS an, nicht ueber `localhost`
- Lokale LLMs werden bevorzugt ueber den Compose-Service `ollama` oder ueber eine nachweislich erreichbare Host-Route angesprochen

## Matrix

| Quelle | Ziel | Bevorzugte URL | Fallbacks | Hinweis |
|---|---|---|---|---|
| Browser auf Host | Frontend | `http://localhost:4200` | - | Angular Dev-/Build-Output |
| Browser auf Host | Hub API | `http://localhost:5000` | - | Standard fuer lokale Bedienung |
| Browser auf Host | Worker APIs | `http://localhost:5001`, `http://localhost:5002` | - | Nur fuer direkte Diagnose |
| Compose-Container | Hub | `http://ai-agent-hub:5000` | `http://localhost:5000` nur im Hub-Container selbst | Compose-DNS statt Host-Loopback |
| Compose-Container | Worker Alpha | `http://ai-agent-alpha:5001` | - | Compose-DNS |
| Compose-Container | Worker Beta | `http://ai-agent-beta:5002` | - | Compose-DNS |
| Compose-Container | Compose-Ollama | `http://ollama:11434/api/generate` | - | Standard fuer Test-/Live-Compose |
| Shell auf Host | Compose-Ollama | `http://localhost:11434/api/generate` | `http://127.0.0.1:11434/api/generate` | Port-Mapping des Containers |
| Container/Tests | Host-Ollama | `OLLAMA_URL` bzw. `E2E_OLLAMA_URL` | `http://host.docker.internal:11434/api/generate`, `http://localhost:11434/api/generate`, `http://127.0.0.1:11434/api/generate` | Nur wenn kein Compose-Ollama verwendet wird |
| Container/Tests | Host-LM-Studio | explizit konfigurierte Host-URL | `host.docker.internal`, funktionierende Host-Gateway-IP | Nur Fallback, wenn Compose-Ollama nicht verwendet wird |

## Compose-Regeln

1. Innerhalb von Compose nie `localhost` fuer andere Services verwenden.
2. Fuer Hub/Worker immer Compose-Service-Namen verwenden.
3. Fuer Live-Tests standardmaessig den Compose-Ollama-Service verwenden.
4. LM Studio ist nur Fallback und sollte nicht als Test-Standard konfiguriert werden.
5. WSL2/Vulkan aendert nur den `ollama`-Service via Overlay, nicht die Hub-Worker-Architektur.
6. `docker-compose.test.yml` kann Host-Port-Publishings explizit entfernen (`ports: !reset []`); in diesem Modus sind `localhost:4200/5000/...` vom Host aus nicht garantiert erreichbar.

## WSL2 / Vulkan

Standardstart mit Compose-Ollama auf WSL2/Vulkan:

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml up -d --build
```

Teststart:

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.ollama-wsl.yml -f docker-compose.test.yml up -d --build
```

Hinweis fuer Docker in nativer WSL2-Distro:
- Wenn Windows `localhost` nicht zur WSL-Instanz durchreicht, nutzen Sie `http://<wsl-ip>:4200` oder richten Sie `portproxy` ein (`setup_host_services.ps1`).

## Live-Test-Reihenfolge fuer Ollama

Die Live-Agent-Chain und weitere Live-LLM-Pfade pruefen in dieser Reihenfolge:

1. `OLLAMA_URL`
2. `E2E_OLLAMA_URL`
3. `http://ollama:11434/api/generate`
4. `http://localhost:11434/api/generate`
5. `http://127.0.0.1:11434/api/generate`
6. `http://host.docker.internal:11434/api/generate`

## Diagnose

- Compose-Service erreichbar: `docker compose ps`
- Hub-Health: `curl -fsS http://localhost:5000/health`
- Compose-Ollama von Host: `curl -fsS http://localhost:11434/api/tags`
- Compose-Ollama aus Container: `curl -fsS http://ollama:11434/api/tags`
- LM Studio nur verwenden, wenn der Models-Endpoint von der relevanten Laufumgebung aus wirklich erreichbar ist
