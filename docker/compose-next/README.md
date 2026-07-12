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
- `compose.voice-restricted.yml` (additives, intern isoliertes Voice- und
  Restricted-Inference-Overlay)

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

## Voice und Restricted Inference

Das Overlay `compose.voice-restricted.yml` ergänzt einen Stack um zwei
voneinander getrennte Ausführungsebenen. Nur der Hub ist mit beiden internen
Netzen verbunden. Angular, normale Worker und die beiden Runtimes können sich
nicht direkt gegenseitig adressieren. Runtime-Ports werden nicht auf dem Host
veröffentlicht; Angular verwendet ausschließlich die Hub-API.

Vor dem Start müssen zwei unterschiedliche, zufällige Service-Tokens und die
lokalen, unveränderlichen Modellverzeichnisse vorhanden sein. Zusätzlich ist
ein eigener Fernet-Key als `VOICE_PERSONALIZATION_ENCRYPTION_KEY` erforderlich.
Er gehört ausschließlich in den Hub und darf weder `SECRET_KEY` noch einem
Runtime-Token entsprechen. Die Compose-Datei legt fehlende Modellverzeichnisse
absichtlich nicht automatisch an:

```text
models/
├── voice/
│   ├── manifests/voice-models.json
│   ├── bin/whisper-cli
│   ├── whisper/ggml-small.bin
│   ├── vosk/
│   └── faster-whisper/
└── restricted-inference/
    ├── manifests/
    └── artifacts/
```

Abweichende Pfade werden mit `VOICE_MODEL_DIR` und
`RESTRICTED_INFERENCE_MODEL_DIR` gesetzt. Beide Mounts sind read-only. Modelle
und Tokenizer müssen vorab geprüft und bereitgestellt werden; die Container
laufen mit `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` und laden beim Start
nichts herunter.

Es gibt drei kombinierte Profile. Pro Stack darf genau eines davon aktiv sein:

- `voice-production-minimal`: Voice über ein lokal gemountetes whisper.cpp;
  Restricted Inference stellt nur Vertrag, Manifestprüfung und Status bereit.
- `voice-production-cpu`: Vosk, whisper.cpp/Faster-Whisper sowie lokale
  sentence-transformers-/ONNX-Ausführung. Dies ist das empfohlene CPU-Profil.
- `voice-production-nvidia`: dieselben isolierten Grenzen mit expliziten NVIDIA-
  Device-Reservierungen. Dafür sind NVIDIA Container Toolkit und passende
  Host-Treiber erforderlich.

Beispiel mit dem Fullstack und CPU-Profil:

```bash
export VOICE_INTERNAL_SERVICE_TOKEN='replace-with-at-least-24-random-characters'
export RESTRICTED_INFERENCE_INTERNAL_TOKEN='replace-with-another-random-token'
export VOICE_PERSONALIZATION_ENCRYPTION_KEY="$(${PYTHON:-python3} -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

INITIAL_ADMIN_PASSWORD=... POSTGRES_PASSWORD=... \
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  --profile voice-production-cpu up -d --build
```

Die Tokens und der Fernet-Key müssen verschieden sein und dürfen nicht committed
werden. Der Fernet-Key wird nur an den Hub übergeben; Voice Runtime, Restricted
Inference, normale Worker und Angular erhalten ihn nicht. Der Hub
erhält intern `http://voice-runtime:8090` und
`http://restricted-inference-worker:8091`; direkte Client-Zugriffe sind
deaktiviert. Beide Runtimes laufen non-root mit read-only Root-Dateisystem,
`cap_drop: ALL`, `no-new-privileges`, begrenztem `tmpfs`, PID-, CPU- und
Speicherlimit sowie eigenen Healthchecks.

CPU- und NVIDIA-Restricted-Inference-Profile werden erst `healthy`, wenn der
Worker `status=ready` meldet; ein lediglich `degraded` gestarteter Executor gibt
den Hub somit nicht für produktive Inferenz frei.

Die drei Profile lassen sich ohne Containerstart und ohne Docker-Daemon-Zugriff
rendern:

```bash
for profile in voice-production-minimal voice-production-cpu voice-production-nvidia; do
  docker compose \
    -f docker/compose-next/compose.stack.full.yml \
    -f docker/compose-next/compose.voice-restricted.yml \
    --profile "$profile" config --quiet
done
```

CPU- und Speichergrenzen können über die in
`compose.voice-restricted.yml` benannten `*_CPUS`- und `*_MEMORY`-Variablen
reduziert werden. Die Runtime-Netze bleiben dabei `internal: true`; nur der Hub
behält zusätzlich das normale Stack-Netz für Datenbank, Redis und explizit
konfigurierte Provider.

Release-Gates, Modellpromotion, Offline-Installation, Streaming-Drain, OOM,
Rollback, Consent/Löschung und die reproduzierbaren CPU-/GPU-Profile sind im
[`Voice/Restricted Production Runbook`](../../docs/operations/voice-restricted-production-runbook.md)
dokumentiert. Hardware-Skips gelten dort nie als bestandener Nachweis.

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
