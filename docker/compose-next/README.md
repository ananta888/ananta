# Compose Next (Dev direkt startbar)

Zwei direkt ausführbare Dev-Umgebungen:

- `compose.dev.lmstudio.yml` (ohne Ollama, mit LM Studio)
- `compose.dev.ollama.yml` (mit lokalem Ollama)

Beide sind für Entwicklung ausgelegt:

- Python-Code wird per Flask-Reloader bei Änderungen neu gestartet (`FLASK_DEBUG=1`).
- Angular läuft mit `ng serve` und aktualisiert automatisch.
- Repo ist als Bind-Mount eingebunden (`../../:/app`).

## Start

```bash
cd docker/compose-next

# LM Studio Dev (standardmäßig http://192.168.178.100:1234/v1)
INITIAL_ADMIN_PASSWORD=... POSTGRES_PASSWORD=... docker compose -f compose.dev.lmstudio.yml up -d --build

# Ollama Dev
INITIAL_ADMIN_PASSWORD=... POSTGRES_PASSWORD=... docker compose -f compose.dev.ollama.yml up -d --build
```

## Stop

```bash
cd docker/compose-next
docker compose -f compose.dev.lmstudio.yml down
docker compose -f compose.dev.ollama.yml down
```

## Hinweise

- LM Studio URL kann überschrieben werden: `LMSTUDIO_URL=http://192.168.178.100:1234/v1`
- Frontend ist unter Port `4200`, Hub unter `5000` erreichbar.
- Hub/Worker-Orchestrierung bleibt unverändert (Hub steuert, Worker führen aus).
