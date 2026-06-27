# Compose Next

`docker/compose-next/` ist die aktive Docker-Compose-Quelle für Ananta.
Neue lokale Starts, Deployments und Release-Builds verwenden ausschließlich
diesen Ordner. Die frühere Compose-Struktur liegt isoliert unter
[`../old_way`](../old_way/README.md).

## Varianten

Direkt ausführbare Dev-Umgebungen:

- `compose.dev.lmstudio.yml` (ohne Ollama, mit LM Studio)
- `compose.dev.ollama.yml` (mit lokalem Ollama)

Deployment-Stacks:

- `compose.stack.quickstart.yml` (SQLite, Hub, zwei Worker, Frontend)
- `compose.stack.full.yml` (PostgreSQL, Redis, Hub, zwei Worker, Frontend)
- `compose.stack.distributed.yml` (PostgreSQL, Redis, Hub, vier Worker, Frontend)

Die Dev-Varianten sind für Entwicklung ausgelegt:

- Python-Code wird per Flask-Reloader bei Änderungen neu gestartet (`FLASK_DEBUG=1`).
- Angular läuft mit `ng serve` und aktualisiert automatisch.
- Repo ist als Bind-Mount eingebunden (`../../:/app`).

## Start

```bash
# Vom Repository-Root:

# Schneller lokaler Start
INITIAL_ADMIN_PASSWORD=... \
docker compose --env-file .env -f docker/compose-next/compose.stack.quickstart.yml up -d --build

# Persistenter Fullstack
INITIAL_ADMIN_PASSWORD=... POSTGRES_PASSWORD=... \
docker compose --env-file .env -f docker/compose-next/compose.stack.full.yml up -d --build

# LM Studio Dev (standardmäßig http://192.168.178.100:1234/v1)
INITIAL_ADMIN_PASSWORD=... POSTGRES_PASSWORD=... \
docker compose --env-file .env -f docker/compose-next/compose.dev.lmstudio.yml up -d --build

# Ollama Dev
INITIAL_ADMIN_PASSWORD=... POSTGRES_PASSWORD=... \
docker compose --env-file .env -f docker/compose-next/compose.dev.ollama.yml up -d --build
```

## Stop

```bash
docker compose --env-file .env -f docker/compose-next/compose.dev.lmstudio.yml down
docker compose --env-file .env -f docker/compose-next/compose.dev.ollama.yml down
```

## Hinweise

- LM Studio URL kann überschrieben werden: `LMSTUDIO_URL=http://192.168.178.100:1234/v1`
- Frontend ist unter Port `4200`, Hub unter `5000` erreichbar.
- Hub/Worker-Orchestrierung bleibt unverändert (Hub steuert, Worker führen aus).
- `compose.base.yml` enthält gemeinsame Definitionen und wird über `extends`
  eingebunden; sie ist kein eigenständiger Startbefehl.
- Das aktive Runtime-Image wird aus
  `docker/compose-next/Dockerfile.quickstart-no-ollama` gebaut.
