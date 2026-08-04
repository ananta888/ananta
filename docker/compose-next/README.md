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
- `compose.workflow-runtime.dev-auth.yml` (Stack-Overlay mit strikten,
  dateibasierten Dev-Identitäten für zusammengesetzte Worker-Stacks)
- `compose.stack.full.yml` (PostgreSQL, Redis, Hub, zwei Worker, Frontend)
- `compose.stack.distributed.yml` (PostgreSQL, Redis, Hub, vier Worker, Frontend)
- `compose.voice-restricted.yml` (additives, intern isoliertes Voice- und
  Restricted-Inference-Overlay)
- `compose.lora-training.yml` (additives, intern authentifiziertes
  LoRA-/QLoRA-Worker-Overlay; genau eines der Profile `lora-training-mock`,
  `lora-training-cpu` oder `lora-training-nvidia` auswählen)
- `compose.temporal.yml` (optionale dauerhafte Workflow-Infrastruktur mit
  Temporal Server, UI, eigener PostgreSQL-Datenbank und Ananta-Worker)
- `compose.temporal.production.yml` (additive produktive Hub-/Temporal-
  Verdrahtung mit externen, read-only Compose-Secrets)
- `compose.workflow-runtime.production.yml` (gemeinsame produktive
  Credential-Allowlist für Hub, Native-Worker und Angular; immer vor den
  Runtime-spezifischen Overlays laden)
- `compose.native.production.yml` (produktiver Native-Runtime-Pfad über die
  Hub-Taskqueue mit getrennten Signing-, Verification- und Dispatch-Secrets)
- `compose.langgraph.production.yml` (dedizierter LangGraph-Worker mit exakt
  gelockter Runtime, Hub-owned Checkpoints und externen read-only Secrets)
- `compose.workflow-runtime-example.yml` (wegwerfbarer, eigenständiger
  Native/LangGraph/Temporal-Drill ohne pytest; die erzeugte Evidence ist
  ausdrücklich kein Production-Release-Gate)
- `compose.workflow-runtime-example.live-provider.yml` (optionaler lokaler,
  OpenAI-kompatibler Provider-Probe mit externer Credential-Datei; weder
  Control Plane noch Taskqueue)

Die Dev-Varianten sind für Entwicklung ausgelegt:

- Python-Code wird per Flask-Reloader bei Änderungen neu gestartet (`FLASK_DEBUG=1`).
- Angular läuft mit `ng serve` und aktualisiert automatisch.
- Laufende Python-Pakete und Konfiguration werden einzeln read-only
  eingebunden. Dadurch bleiben Hot Reload und Host-Änderungen sichtbar,
  während `.env`, lokale Schlüssel, Git-Metadaten und Modellblobs nicht in
  Hub- oder Worker-Container gelangen. Nur `project-workspaces` ist für
  delegierte Arbeitsartefakte schreibbar.

## WSL2-Docker-Daemon

Unter aktuellem WSL2 soll genau das native, über `/etc/wsl.conf` aktivierte
systemd PID 1 sein. Ein älterer Shell-Hook wie
`/usr/sbin/start-systemd-namespace` in `/etc/bash.bashrc` darf nicht parallel
weiterlaufen. Die Kombination startet eine zweite systemd-/Docker-Umgebung,
erzeugt konkurrierende Sockets und kann `docker info` trotz laufender Prozesse
hängen lassen.

Sichere Diagnose ohne Änderung oder Datenverlust:

```bash
ps -p 1 -o comm=
systemctl is-active systemd-logind containerd docker
timeout 10 docker info
rg -n 'start-systemd-namespace' /etc/bash.bashrc /etc/profile.d 2>/dev/null
```

Wenn PID 1 bereits `systemd` ist und der alte Hook noch geladen wird:

1. `/etc/bash.bashrc` mit `sudo cp -a` unter einem eindeutigen Namen sichern.
2. Nur die Zeile deaktivieren, die `start-systemd-namespace` lädt.
3. In Windows PowerShell `wsl.exe --shutdown` ausführen und Ubuntu neu öffnen.
4. Die obigen Prüfungen sowie `docker version` erneut ausführen.
5. Erst danach den Stack mit seinem expliziten Compose-Pfad starten.

Diese Reparatur benötigt weder das Löschen noch das Neuerstellen von
Docker-Volumes oder Bind-Mount-Daten. Befehle wie `docker system prune
--volumes` gehören ausdrücklich nicht zu diesem Ablauf. Weitere
Windows-/WSL-Hinweise stehen in
[`../../docs/DOCKER_WINDOWS.md`](../../docs/DOCKER_WINDOWS.md).

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

Der Ollama-Stack lädt beim ersten Start `phi4-mini` sowie
`gemma4:e4b-it-qat` und erzeugt die lokalen Aliase
`ananta-phi4-mini-32k` und `ananta-gemma4-reasoning-8k`. Mit
`OLLAMA_DATA_DIR=/home/<user>/ananta-data/ollama` liegt der Modellspeicher als
normaler WSL2-Bind-Mount außerhalb von Docker-Volumes und kann bei gestopptem
Ollama-Container direkt gesichert werden. Ohne diese Variable wird
`../../../ananta-data/ollama` relativ zur Compose-Datei verwendet. Die
Long-Syntax erzwingt dabei auch für benutzerdefinierte relative Werte einen
Host-Bind-Mount statt eines Docker-Volumes.

Bereits vorhandene Basismodelle werden beim Start nicht erneut aus der
Registry geladen. Mit `OLLAMA_BOOTSTRAP_OFFLINE=1` sind Netzwerkzugriffe des
Modell-Bootstraps deaktiviert; fehlt dann ein erforderliches Basismodell,
bricht der Stack absichtlich ab. Der frühere GGUF-Autoimport läuft in diesem
dedizierten Phi-/Gemma-Stack nicht mit.

Vor dem Hub-Start erzeugt ein einmaliger Bootstrap automatisch getrennte
Workflow-Keyrings unter
`${ANANTA_DEV_WORKFLOW_SECRET_DIR:-../../../ananta-data/workflow-runtime-dev}`.
Der Hub erhält den privaten Ed25519-Signing- und Dispatch-Keyring, sein eigenes
Service-/Session-Secret sowie das Worker-Registrierungs-Keyring. Jeder Worker
erhält ausschließlich den öffentlichen Verification-Keyring und ein eigenes,
von allen anderen Identitäten getrenntes Service-, Registrierungs- und
Session-Secret. Strikte registrierte Worker-Authentisierung ist im Stack
aktiv; inline Secrets und persistierte Token-Rotation sind deaktiviert.
Aktuelle vollständige Credentials werden validiert und unverändert
wiederverwendet. Ein vollständig bekannter Vorgängerstand erhält nur die
neuen Vector-Capability-Grants; Schlüssel und Tokens rotieren dabei nicht.
Ein alter Satz nur der drei Authorization-Keyrings wird transaktional um die
Identitätsdateien ergänzt. Jeder andere unvollständige oder veränderte Satz
bricht fail-closed ab.

Da die strikte Worker-Authentisierung aktiv ist, muss `CORS_ORIGINS` eine
explizite, kommaseparierte Browser-Origin-Liste enthalten. Die lokalen
Standardwerte erlauben `http://localhost:4200` und
`http://127.0.0.1:4200`; bei einem anderen Frontend-Port ist die Variable
anzupassen.

## Backup und isolierter Restore-Drill

Ein Backup nur von `data/` oder nur der Docker-Volumes ist für diesen Stack
unvollständig. Die Host-CLI `scripts/ananta-backup.py` erfasst PostgreSQL,
Hub-/Worker-Volumes, Workflow-Credentials, Projekt-Workspaces sowie die
reproduzierbare Konfiguration und veröffentlicht ausschließlich ein
OpenPGP-verschlüsseltes Paket in WSL und auf dem Windows-Known-Folder-Desktop.
Das WSL-Ziel muss außerhalb `/mnt` liegen.

Routinemäßige State-Backups lassen Ollama-Modellblobs aus. Für ein zusätzliches
separates Paket nimmt `--include-ollama-models` ausschließlich
`.ollama/models` auf; die Ollama-Hostidentität `id_ed25519` bleibt immer
ausgeschlossen. `scripts/ananta-restore.py` führt ausschließlich einen
isolierten Restore-Drill in ein neues oder leeres WSL-Verzeichnis aus und
importiert nichts in den Live-Stack.

Key-Verwaltung, exakte Zielregeln, Offline-Kopien, Restore-Prüfungen und
bekannte Grenzen beschreibt das
[`lokale Backup-/Restore-Runbook`](../../docs/operator/local-backup-restore.md).
Bitcoin-Core-Wallets gehören noch nicht zu diesem Stack und werden nicht
mitgesichert.

Der Bootstrap überträgt die Besitzrechte anschließend an
`${ANANTA_HOST_UID:-1000}:${ANANTA_HOST_GID:-1000}`. Für einen abweichenden
WSL-Benutzer sind beide Werte in `.env` auf die Ausgabe von `id -u` und
`id -g` zu setzen; die privaten Dateimodi bleiben dabei `0600`.

Im lokalen Ollama-Stack verwenden Alpha und Beta jeweils eine eigene
SQLite-Datenbank im zugehörigen `alpha-data`- beziehungsweise
`beta-data`-Volume. Ohne explizite Migrationsvorgabe bleibt das bisherige
Entrypoint-Verhalten dieses und anderer Stacks erhalten.

Die aktuell verwendete Kombination aus `compose.tests.lmstudio.yml` und
`compose.workflow-runtime.dev-auth.yml` bindet Hub und Worker dagegen an
dieselbe PostgreSQL-Datenbank. In diesem Overlay ist ausschließlich der Hub
Schema-Owner: `ANANTA_RUN_DB_MIGRATIONS=1` lässt ihn vor dem Start
`alembic upgrade head` ausführen; für Alpha und Beta verhindert der Wert `0`
parallele Schema-Writes. Die im Overlay gesetzten `command`-Werte enthalten
keine Schema-Writes. Alpha und Beta führen sie im effektiven Modus
`agent-only` direkt aus. Der Hub läuft im Modus `role`, ignoriert `command`
vollständig und startet die Anwendung nach seiner Entrypoint-Migrationsphase;
Rolle und Startpfad bestimmt ausschließlich `ANANTA_QUICKSTART_ROLE`. Das
Credential-Verzeichnis ist über `.gitignore` ausgeschlossen, gehört aber zu
einer vollständigen lokalen Laufzeitsicherung. `.dockerignore` schließt es
zusätzlich aus jedem Image-Build-Kontext aus. Diese automatisch erzeugten
Entwicklungs-Credentials dürfen nicht in Produktion übernommen werden.

Das LoRA-Overlay wird ausschließlich über
`scripts/run-lora-training-stack.sh <Profil>` gestartet. Der Wrapper bindet
Hub-Modus, Backend-Allowlist und Ressourcenprofil an denselben Worker und
verhindert einen versehentlichen CPU-/NVIDIA-Start mit Mock-Hub-Defaults.

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

## Native Runtime

Alle produktiven Workflow-Runtimes verwenden zuerst
`compose.workflow-runtime.production.yml`. Diese eine Security-Allowlist ersetzt
die aus `compose.base.yml` geerbte Entwicklungsumgebung: nur der Hub behält
Postgres-, Redis- und Initial-Admin-Zugang. Alpha, Beta und Angular erhalten
weder diese Werte noch einen inline `SECRET_KEY`. Hub und Worker laden jeweils
einen eigenen Session-/JWT-Key über `SECRET_KEY_FILE`.

Alpha und Beta besitzen außerdem je ein einmaliges Registrierungs-Token und ein
eigenes Service-Token. Das Registrierungs-Token ist ausschließlich für das
Binden von Worker-ID und Worker-URL bestimmt; nur das danach registrierte
Service-Token authentifiziert die eng begrenzten Workflow-Routen. Das
Hub-Admin-Service-Token bleibt ausschließlich im Hub.

Der gemeinsame Produktionslayer entfernt außerdem alle Host-Ports von
PostgreSQL, Redis, Alpha und Beta. Nur der Hub bleibt im Stack-Datennetz; Alpha
und Beta erreichen ihn über das getrennte `workflow-worker-control`-Netz.
LangGraph bleibt ausschließlich in `langgraph-runtime`, der Temporal-Worker
ausschließlich in `temporal-runtime`. So kann ein kompromittierter Worker weder
den unauthentisierten Redis-Zustand noch PostgreSQL direkt erreichen.
Angular erreicht ausschließlich den Hub über `workflow-ui-control`; es teilt
weder das Daten- noch ein Worker-Netz. Der Produktionslayer entfernt außerdem
die schreibbaren Entwicklungs-Bind-Mounts des Frontends und lässt die Host-
Prüfung des Angular-Servers aktiviert. Die Dev-Overlays behalten Hot Reload und
ihre Bind-Mounts unverändert bei.
Der Layer setzt zusätzlich `ANANTA_QUICKSTART_MODE=role` und für Hub, Worker
und Angular jeweils die explizite `ANANTA_QUICKSTART_ROLE`; das Image-Default
`hub` darf niemals die Compose-Service-Rolle bestimmen.

Die folgenden Quelldateipfade sind für den gemeinsamen Layer erforderlich:

```bash
export CORS_ORIGINS=https://ananta.example.org
export ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-auth-signing-keyring.json
export ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-auth-verification-keyring.json
export ANANTA_WORKFLOW_DISPATCH_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-dispatch-keyring.json
export ANANTA_WORKFLOW_HUB_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-hub-service-token
export ANANTA_HUB_SESSION_SIGNING_KEY_SECRET_FILE=/etc/ananta/secrets/workflow-hub-session-signing-key
export ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-worker-registration-keyring.json
export ANANTA_WORKFLOW_WORKER_ALPHA_REGISTRATION_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-alpha-registration-token
export ANANTA_WORKFLOW_WORKER_BETA_REGISTRATION_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-beta-registration-token
export ANANTA_WORKFLOW_WORKER_ALPHA_SERVICE_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-alpha-service-token
export ANANTA_WORKFLOW_WORKER_BETA_SERVICE_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-beta-service-token
export ANANTA_WORKER_ALPHA_SESSION_SIGNING_KEY_SECRET_FILE=/etc/ananta/secrets/workflow-worker-alpha-session-signing-key
export ANANTA_WORKER_BETA_SESSION_SIGNING_KEY_SECRET_FILE=/etc/ananta/secrets/workflow-worker-beta-session-signing-key

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.native.production.yml \
  config --quiet
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.native.production.yml \
  up -d --build
```

`CORS_ORIGINS` ist wegen credentialed CORS und der OIDC-Session-Cookies
verpflichtend und muss ausschließlich vertrauenswürdige, vollständige Origins
enthalten; `*` ist für diesen Produktionslayer unzulässig.

Der gemeinsame Production-Layer entfernt außerdem den Entwicklungs-Bind-Mount
aus allen Workern. Alpha, Beta und der optionale LangGraph-Worker erhalten je
ein eigenes `nocopy` Named Volume als vollständigen `/project-workspaces`-Root;
der Temporal-Worker erhält keinen Workspace-Mount. Nur der Hub behält den
operatorseitigen Projekt-Bind. Eingaben und Ergebnisse zwischen Identitäten
müssen daher über Hub-Tasks, Kontext- und Artefaktverträge fließen. Die drei
Worker-Volumes sind bei Backup, Tenant-Retention und `docker compose down`
explizit zu berücksichtigen; sie dürfen nicht zwischen Worker-Identitäten
wiederverwendet werden.

Das Registrierungs-Keyring hat das Schema
`ananta.workflow-worker-registration-keyring.v1`. Unter `workers` muss jede
tatsächliche `AGENT_NAME` exakt auf ihre interne `worker_url` und auf den Inhalt
der zugehörigen Registrierungs-Token-Datei zeigen. Zusätzlich sind
`service_token_sha256` und `session_signing_key_sha256` verpflichtend. Sie sind
die kleingeschriebenen SHA-256-Hex-Digests des jeweils getrimmten Inhalts der
zugehörigen Worker-Service-Token- bzw. Worker-Session-Key-Datei. Der Hub erhält
nur diese Fingerprints, niemals die beiden rohen Worker-Secrets. Beispiel für
eine bereits erzeugte, whitespace-freie Quelldatei:

```bash
python - <<'PY'
import hashlib
from pathlib import Path

for name in (
    "workflow-worker-alpha-service-token",
    "workflow-worker-alpha-session-signing-key",
):
    value = Path("/etc/ananta/secrets", name).read_text(encoding="utf-8").strip()
    if not value or any(character.isspace() for character in value):
        raise SystemExit(f"invalid secret content: {name}")
    print(name, hashlib.sha256(value.encode("utf-8")).hexdigest())
PY
```

Die Ausgabe wird ausschließlich in die passenden Fingerprint-Felder des
Keyrings übernommen. Bei jeder Rotation eines Worker-Service-Tokens oder
Session-Keys müssen Secret-Datei und Fingerprint atomar gemeinsam aktualisiert
und Hub sowie betroffener Worker anschließend neu gestartet werden; ein
Mischstand wird fail-closed abgelehnt. `allowed_capabilities` ist
die vollständige Hub-Allowlist, nicht die Selbstauskunft des Workers. Alpha und
Beta erhalten exakt `planning, analysis, research, source_analysis, coding, implementation,
review, testing, verification, workflow.adapter.native, approval,
bounded_parallel, checkpoint, deterministic_merge, resume, retrieval, stream,
index_write, structured_output, subgraphs, tool_calling,
vector_index_operation`. `source_analysis` bezeichnet ausschließlich die
Analyse des vom Hub freigegebenen, task-gebundenen Quellenkontexts und erteilt
weder Source-ID-Erzeugung noch Netzwerk- oder Orchestrierungsrechte.
`vector_index_operation` wird vom Worker nur
angemeldet, wenn der öffentliche Attestierungs-Keyring und die
Hub-Dispatch-Zulassung erfolgreich zusammengesetzt wurden. Der dedizierte LangGraph-Worker
erhält nur dieselben semantischen Basis-Capabilities plus
`workflow.adapter.langgraph`. Erfolgreiche strikte Registrierung wird als
`strict_registration_keyring_v1` persistiert. Legacy-/Default-Datenbankzeilen
mit Provenienz `legacy` dürfen niemals scoped Worker-Routen authentifizieren.
Token und Session-Key sind jeweils eigenständig, whitespace-frei und mindestens
32 Byte lang; keine zwei Zwecke oder Worker dürfen denselben Wert verwenden.
Der Hub validiert diese Trennung beim Start über seinen User-Session-Key, sein
Service-Token, alle persistierten Worker-Service-Tokens sowie Registrierungs-
und Runtime-Keyrings. Eine Kollision beendet den Start; eine neu eingehende
Worker-Registrierung wird ebenfalls vor der Persistierung abgelehnt.

Alle Secret-Quellen sind absolute, reguläre Dateien mit genau einem Hardlink.
Der Besitzer ist `root` oder die effektive Container-UID; Gruppen- und
World-Schreibrechte sind verboten. Für die aktuell als root laufenden
Hub/Agent-Container ist `root:root 0600` der Standard. Der öffentliche
Verification-Keyring darf `root:root 0444` sein, damit auch der non-root
Temporal-Worker ihn lesen kann. Bei lokalem Docker Compose sind `uid`, `gid`
und `mode` am Secret-Mount keine Berechtigungsüberschreibung: maßgeblich bleiben
Besitzer und Modus der Quelldatei auf dem Host. Ein unsicherer Pfad, Symlink,
Hardlink, Größenfehler oder eine Änderung während des Lesens beendet den Start
fail-closed.

## LangGraph Runtime

Das produktive LangGraph-Overlay ist additiv und startet genau einen dedizierten
Worker im Profil `langgraph`. Nur Hub und dieser Worker teilen das
`langgraph-runtime`-Netz. Der Worker veröffentlicht keinen Host-Port, erhält
nur den öffentlichen Ed25519-Verifikationsschlüsselring und eigene
Registrierungs-, Service- und Session-Secrets. Der private Signaturschlüssel,
Dispatch-Key und Hub-Admin-Token bleiben ausschließlich im Hub.

```bash
export ANANTA_WORKFLOW_WORKER_LANGGRAPH_REGISTRATION_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-langgraph-registration-token
export ANANTA_WORKFLOW_WORKER_LANGGRAPH_SERVICE_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-worker-langgraph-service-token
export ANANTA_WORKER_LANGGRAPH_SESSION_SIGNING_KEY_SECRET_FILE=/etc/ananta/secrets/workflow-worker-langgraph-session-signing-key

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.langgraph.production.yml \
  --profile langgraph config --quiet
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.langgraph.production.yml \
  --profile langgraph up -d --build
```

Das Basis-Image bleibt ohne LangGraph. Nur das dedizierte Worker-Image setzt
`INSTALL_LANGGRAPH_RUNTIME=1` und installiert den exakten additiven Lock. Die
Provider-Konfiguration muss danach explizit `state_policy=hub_owned` und
`checkpoint_policy=hub_owned` aktivieren. Details, Prüfungen, Rotation und
Recovery stehen im
[`LangGraph Hub-owned Checkpoint Runbook`](../../docs/operations/langgraph-hub-checkpoint-runtime.md).

## Temporal Runtime

Temporal ist ein additives Profil und ersetzt weder den Hub noch dessen
Taskqueue. Der Temporal Worker registriert technische Workflows und übergibt
ausführbare Schritte ausschließlich als autorisierte Hub-Tasks. Ohne
Schlüsselring und scoped Runtime-Service-Token bleibt die produktive Activity
fail-closed; der side-effect-freie Probe-Workflow funktioniert trotzdem.

`compose.temporal.yml` bleibt bewusst ohne produktive Credentials und kann den
side-effect-freien Probe-Workflow ausführen. Eine produktive Activity wird erst
mit dem zusätzlichen `compose.temporal.production.yml` aktiviert. Zusätzlich
zum gemeinsamen Security-Layer sind zwei Quelldateien erforderlich:

```bash
export ANANTA_WORKFLOW_RUNTIME_SERVICE_KEYRING_SECRET_FILE=/etc/ananta/secrets/workflow-runtime-service-keyring.json
export ANANTA_WORKFLOW_TEMPORAL_SERVICE_TOKEN_SECRET_FILE=/etc/ananta/secrets/workflow-temporal-service-token

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.temporal.production.yml \
  --profile temporal config --quiet
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.temporal.production.yml \
  --profile temporal up -d --build
```

Das Runtime-Service-Keyring verwendet das Schema
`ananta.workflow-runtime-service-keyring.v1` und enthält unter `services` genau
den Eintrag `ananta-temporal-worker` mit dem Inhalt der separaten Token-Datei
und ausschließlich dem Scope `workflow.temporal.tasks`. Der Temporal Worker
sendet dazu `X-Ananta-Service-ID: ananta-temporal-worker`; das Token ist weder
ein Hub-Admin-Token noch ein registriertes Agent-Token. Die Token-Quelldatei
muss für den Container-User `10001` lesbar sein, empfohlen ist
`10001:10001 0600`; das Hub-only Keyring bleibt `root:root 0600`.

Zusätzlich müssen `TEMPORAL_POSTGRES_PASSWORD`, `POSTGRES_PASSWORD` und
`INITIAL_ADMIN_PASSWORD` wie im Full-Stack gesetzt sein.

Alle drei Runtimes können unter demselben Hub gerendert und gestartet werden.
Die Reihenfolge ist verbindlich: Stack, gemeinsamer Security-Layer, Native,
LangGraph, Temporal-Basis und zuletzt Temporal-Produktion. Das letzte Overlay
wählt Temporal als durable Orchestration-Backend; Native- und LangGraph-Worker
bleiben weiterhin Hub-kontrollierte Ausführer:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.workflow-runtime.production.yml \
  -f docker/compose-next/compose.native.production.yml \
  -f docker/compose-next/compose.langgraph.production.yml \
  -f docker/compose-next/compose.temporal.yml \
  -f docker/compose-next/compose.temporal.production.yml \
  --profile langgraph --profile temporal config --quiet
```

Die Temporal UI ist standardmäßig unter `http://localhost:8233` erreichbar.
Produktive Verbindungen konfigurieren TLS/mTLS, API-Key und Hub-Credentials
ausschließlich als absolute Secret-Dateireferenzen. Das
vollständige Betriebs- und Failure-Runbook liegt unter
[`docs/operations/temporal-runtime.md`](../../docs/operations/temporal-runtime.md).
Das One-shot-Gate in `compose.tests.temporal.yml` startet einen echten Probe-
Workflow und beendet CI mit dessen Exit-Code; der genaue Aufruf steht im
Runbook.

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
