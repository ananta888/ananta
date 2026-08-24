# KAT-Coder v2.5 mit Colibrì auf der lokalen RTX 3080

Diese Seite beschreibt den auf diesem Ananta-Rechner verwendeten Aufbau für
**KAT-Coder-V2.5-Dev** mit der **Colibrì-Runtime**. Sie umfasst das verwendete
Modellformat, die MoE- und Expert-Tier-Architektur, die benötigten Dateien, den
CUDA-Build, den lokalen OpenAI-kompatiblen Server, die Ananta-Integration und
die gemessenen Betriebsgrenzen.

Die Seite ist absichtlich KAT-spezifisch. Der gemeinsame Betrieb mit LFM2.5
und Needle 2 ist unter
[`local-kat-lfm-needle-runtime.md`](local-kat-lfm-needle-runtime.md)
dokumentiert.

## Kurzfassung des installierten Stands

| Eigenschaft | Lokaler Stand |
|---|---|
| Modell | `KAT-Coder-V2.5-Dev` |
| Grundlage | Qwen3.6-35B-A3B / `qwen3_5_moe_text` |
| Lizenz der offenen Gewichte | Apache-2.0 |
| Format | Colibrì group-scaled INT4, `expert_gs=64` |
| Modellverzeichnis | `/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64` |
| Größe auf diesem Host | ungefähr 22 GB |
| Colibrì-Binaries | `/home/krusty/moe-test/colibri/c/coli` und `qwen36` |
| API-Modell-ID | `kat-coder-v2.5-dev` |
| API | OpenAI-kompatibel auf Port `8082` |
| Ananta-Profil | `local_kat_coder_v25_heavy` |
| Kontext im Ananta-Profil | 32.768 Tokens |
| Exklusiver Messwert | ungefähr 12,14 Decode-Tokens/s mit 8-GiB-Expert-Tier |
| Gemeinsamer Betriebsdefault | 4-GiB-Expert-Tier, damit LFM parallel Platz hat |

Wichtig: Installiert ist die offene **Dev-Version**. Sie darf nicht mit dem
kommerziellen KAT-Coder-V2.5-Flaggschiff oder dessen Benchmarkwerten
gleichgesetzt werden.

## Was Colibrì in diesem Aufbau leistet

KAT-Coder ist hier kein GGUF-Modell und wird nicht durch llama.cpp geladen.
Das Modell liegt als Colibrì-Container mit geshardeten Safetensors,
Tokenizer, Metadaten und Routing-Heat-Profil vor. Colibrì stellt dafür zwei
wesentliche Komponenten bereit:

1. `qwen36` implementiert die Qwen3.6-MoE-Inferenz einschließlich des
   group-scaled-INT4-Formats.
2. `coli serve` legt um diese Engine einen OpenAI-kompatiblen HTTP-Server, den
   Ananta wie einen lokalen Modellprovider anspricht.

Das verwendete gs64-Format quantisiert die Expert-Gewichte auf INT4 und
speichert eine Float32-Skalierung je Gruppe von 64 Eingangselementen. Dadurch
passt der gesamte 35B-MoE-Checkpoint in ungefähr 22 GB auf Massenspeicher,
während zur Laufzeit nur ein konfigurierbarer heißer Teil der Experten im
VRAM liegt.

### Layer, Experten und aktiver Parametersatz

Die installierte `config.json` beschreibt:

- 40 Transformer-Layer;
- 256 geroutete Experten je Layer;
- Top-8-Routing, also acht ausgewählte Experten je Token und MoE-Layer;
- 35 Milliarden Gesamtparameter, aber ungefähr 3 Milliarden aktive Parameter
  pro Token;
- drei lineare Attention-Layer, gefolgt von einem Full-Attention-Layer;
- einen nativen Maximal-Kontext von 262.144 Tokens im Modellformat.

Damit existieren rechnerisch `40 × 256 = 10.240` layergebundene
Expert-Blöcke. Das sind keine 10.240 Fachgebiete wie Biologie oder Chemie.
Ein Expert ist ein trainierter Feed-forward-Teilblock. Der Router wählt für
jeden Token pro Layer acht davon anhand gelernter Aktivierungsmuster aus.
Auch gleich nummerierte Experten in verschiedenen Layern sind unterschiedliche
Gewichtsblöcke.

Der Ananta-Server begrenzt den Kontext derzeit bewusst auf 32k. Der größere
theoretische Modellkontext ist damit nicht als produktiv getestete
Ananta-Kapazität zu verstehen.

### RAM- und VRAM-Aufteilung

Alle 10.240 Expert-Blöcke bleiben grundsätzlich über den Host-RAM erreichbar.
Colibrì kopiert die anhand des Routing-Heat-Profils wichtigsten Experten in
einen persistenten CUDA-Tier. Treffer laufen auf der GPU; nicht residente
Experten fallen auf den CPU-Pfad zurück. Der Expert-Tier verändert weder die
Routerentscheidung noch die Gewichte, sondern nur den Ausführungsort.

`CUDA_EXPERT_GB` ist daher ein Speicherbudget und keine Anzahl von Experten.
Wie viele Blöcke hineinpassen, hängt von deren Größe und dem übrigen
CUDA-Speicher ab. Beim gemeinsamen Smoke passten mit 4 GiB Budget 2.421 der
10.240 Expert-Blöcke in den CUDA-Tier. Das bedeutet nicht, dass 2.421 Experten
gleichzeitig für einen Token rechnen: Aktiv bleiben weiterhin höchstens acht
pro betroffenem Layer.

`heat.bin` speichert beobachtete Routing-Häufigkeiten. Ein Warmstart kann
dadurch schon vor dem ersten Token bevorzugt häufig verwendete Experten laden.
Fehlt oder veraltet diese Datei, bleibt das Modell prinzipiell lauffähig, aber
Platzierung, Trefferrate und Durchsatz können schlechter sein.

## Benötigte Dateien und Programme

Das Ananta-Startskript prüft vor dem Start mindestens:

```text
/home/krusty/moe-test/colibri/c/coli
/home/krusty/moe-test/colibri/c/qwen36
/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64/config.json
/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64/heat.bin
```

Zum vollständigen Modellverzeichnis gehören außerdem:

- `tokenizer.json`;
- `qwen36_meta.json` mit Colibrì-spezifischen Formatangaben;
- `config.hf.json` und `config.json`;
- `model-globals.safetensors` für die gemeinsamen Modellgewichte;
- `model-00000.safetensors` bis `model-00039.safetensors` für die
  layerbezogenen Shards.

Für den GPU-Betrieb werden außerdem ein passender NVIDIA-Treiber, ein
CUDA-Toolkit, `nvidia-smi`, ausreichend Host-RAM und freier VRAM benötigt.
Der lokale Aufbau verwendet zusätzlich die CUDA-Libraries unter
`/home/krusty/moe-test/colibri/.cuda-toolkit/lib`.

## Reproduzierbarer Colibrì-Build

Der gs64-CUDA-Pfad benötigt die Colibrì-Variante mit Qwen3.6-Engine,
gs64-Expert-Tier und korrigiertem asynchronem grouped-INT4-Backend. Der dem
Modell beiliegende Upstream-Hinweis nennt dafür den Branch `gs64-gpu` und den
Stand `de5dde7`. Ein beliebiger älterer Colibrì-Build ist nicht austauschbar:
ohne den grouped-INT4-Fix kann der GPU-Pfad falsche Skalierungen verwenden.

Ein Neuaufbau erfolgt im Colibrì-Checkout beispielsweise so:

```bash
cd /home/krusty/moe-test/colibri
make -C c qwen36 CUDA=1 CUDA_ARCH=native
```

Alternativ beschreibt die Modellkarte den expliziten Build:

```bash
cd /home/krusty/moe-test/colibri/c
nvcc -O3 -std=c++17 -arch=native -c backend_cuda.cu -o backend_cuda.o
gcc -O3 -march=x86-64-v3 -fopenmp -pthread \
  qwen36.c qwen36_tier.c vulkan_gemv.c backend_cuda.o \
  -o qwen36 -lm -lcudart -lstdc++
```

Nach einem Rebuild sollten zuerst die Colibrì-CUDA-Kerneltests und danach ein
kurzer CPU-/GPU-Ausgabevergleich laufen. Ein bloß erfolgreich gestartetes
Binary beweist noch keine korrekte gs64-Inferenz.

## Direkter Colibrì-Smoke ohne Ananta

Für einen Engine-Test kann `qwen36` direkt verwendet werden. `cap` muss bei
dieser Architektur 256 entsprechen, weil alle Experten im RAM adressierbar
bleiben müssen:

```bash
cd /home/krusty/moe-test/colibri/c
printf '%s\n' \
  'Write a Python function that returns the n-th Fibonacci number using memoization.' \
  > /tmp/kat-prompt.txt

SNAP=/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64 \
COLI_GPUS=0 \
COLI_CUDA=1 \
CUDA_EXPERT_GB=8 \
CUDA_RESERVE_GB=2 \
HEAT_FILE=/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64/heat.bin \
OMP_NUM_THREADS=12 \
N_NEW=200 \
./qwen36 256 4 /tmp/kat-prompt.txt
```

Dieser 8-GiB-Modus ist für den exklusiven KAT-Test gedacht. Er ist nicht der
sichere Parallel-Default für KAT und LFM auf derselben RTX 3080.

## Betrieb über den Ananta-Operatoradapter

Der normale Betrieb soll nicht durch frei zusammengesetzte Einzelbefehle,
sondern über [`scripts/local-multi-model-runtime.sh`](../../scripts/local-multi-model-runtime.sh)
erfolgen. Das Skript besitzt Preflight, feste Startreihenfolge, Readiness,
PID-Dateien, Logs und kontrolliertes Stoppen.

Nur KAT allein lässt sich mit demselben `coli serve`-Aufruf nachvollziehen:

```bash
export ANANTA_LOCAL_MODEL_API_KEY='<mindestens 24 zufällige Zeichen>'

COLI_MODEL=/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64 \
COLI_GPUS=0 \
COLI_CUDA=1 \
COLI_API_KEY="$ANANTA_LOCAL_MODEL_API_KEY" \
CUDA_EXPERT_GB=8 \
CUDA_RESERVE_GB=2 \
HEAT_FILE=/home/krusty/moe-test/colibri/models/kat_coder_v2_5_i4_gs64/heat.bin \
OMP_NUM_THREADS=12 \
/home/krusty/moe-test/colibri/c/coli serve \
  --host 127.0.0.1 \
  --port 8082 \
  --model-id kat-coder-v2.5-dev \
  --ctx 32768 \
  --cap 256
```

Für den gemeinsamen produktiven Aufbau gelten dagegen:

```bash
export ANANTA_LOCAL_MODEL_API_KEY='<mindestens 24 zufällige Zeichen>'
export ANANTA_NEEDLE_TOKEN="$ANANTA_LOCAL_MODEL_API_KEY"
export ANANTA_LOCAL_MODEL_BIND_HOST=172.17.0.1
export ANANTA_KAT_EXPERT_GB=4

scripts/local-multi-model-runtime.sh preflight
scripts/local-multi-model-runtime.sh start
scripts/local-multi-model-runtime.sh status
```

Die Bridge-Adresse muss zur lokalen Docker-Konfiguration passen. Bei einer
Nicht-Loopback-Bindung verweigert das Skript den Start ohne mindestens 24
Zeichen langen API-Key. Zugangsdaten gehören ausschließlich in die lokale
Umgebung beziehungsweise `.env` und niemals in Git oder Logs.

Das Skript startet im gemeinsamen Aufbau zuerst das dichte LFM-Modell und
danach KAT. So wird LFM zuerst fest im VRAM platziert; KAT erhält anschließend
nur seinen begrenzten Expert-Tier. Die umgekehrte Reihenfolge könnte KAT zu
viel VRAM reservieren und den LFM-Start verhindern.

### Readiness und API-Test

Readiness wird über den echten Modellendpunkt geprüft:

```bash
curl --fail \
  -H "Authorization: Bearer $ANANTA_LOCAL_MODEL_API_KEY" \
  http://127.0.0.1:8082/v1/models
```

Eine kurze Generierung kann OpenAI-kompatibel angefordert werden:

```bash
curl --fail http://127.0.0.1:8082/v1/chat/completions \
  -H "Authorization: Bearer $ANANTA_LOCAL_MODEL_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "kat-coder-v2.5-dev",
    "messages": [{"role": "user", "content": "Implementiere eine kleine Python-Binärsuche."}],
    "temperature": 0.1,
    "max_tokens": 128
  }'
```

## Verdrahtung in Ananta

KAT bleibt ein Modellprovider. Der Hub behält Routing, Taskqueue, Policies und
Delegation; weder Colibrì noch ein Worker erhalten Orchestrierungsautorität.

Das Profil
[`local-kat-lfm-needle-rtx3080.model_profiles.yaml`](../../config/models/local-kat-lfm-needle-rtx3080.model_profiles.yaml)
deklariert KAT als lokalen, cloudfreien Coder mit:

- OpenAI-kompatiblem Provider;
- 32k Kontext und maximal 8.192 Output-Tokens;
- 300 Sekunden Timeout;
- `supports_json: true`;
- `supports_tools: false` und `tool_calling_mode: prompt_json`;
- bevorzugten Aufgaben `architecture`, `coding`, `debugging`, `planning`,
  `reasoning` und `repo_analysis`.

`supports_tools: false` bedeutet: Colibrì/KAT führt nicht selbstständig
Ananta-Tools aus. Ein Hub-/Worker-Tool-Loop kann KAT Kontext und Toolresultate
im Prompt geben, aber autorisierte Toolausführung bleibt in Anantas bestehendem
Gateway.

Die Routingregeln in
[`local-kat-lfm-needle-rtx3080.model_routing.json`](../../config/models/local-kat-lfm-needle-rtx3080.model_routing.json)
weisen Coding, Debugging, Planung und Repository-Analyse KAT zu. Ein additives
Compose-Overlay setzt diese Profil- und Routingpfade in Hub, Alpha und Beta:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.base.yml \
  -f docker/compose-next/compose.dev.lmstudio.yml \
  -f docker/compose-next/compose.dev-domain.yml \
  -f docker/compose-next/compose.local-kat-lfm-needle.yml up -d
```

Innerhalb der Container lautet die Provideradresse
`http://host.docker.internal:8082/v1`. `127.0.0.1` wäre dort der jeweilige
Container und deshalb falsch.

## Gemessene Werte und richtige Interpretation

### Exklusiver Einzeltest

KAT erreichte auf diesem Rechner ungefähr **12,14 Decode-Tokens/s**, als ihm
ein exklusiver **8-GiB-CUDA-Expert-Tier** zur Verfügung stand. Dieser Wert ist
eine lokale Einzelmessung und keine allgemeine Modellgarantie. Promptlänge,
Heat-Profil, CPU, RAM-Bandbreite, Expert-Hitrate und Antwortmuster beeinflussen
den Durchsatz.

### Gemeinsamer KAT-/LFM-/Needle-Smoke

Für den Parallelbetrieb war 8 GiB nicht verwendbar, weil auch LFM dauerhaft
VRAM benötigt. Ein zunächst geprüfter 5-GiB-KAT-Tier ließ nur 921 MiB frei und
verletzte damit die geforderte Reserve. Der sichere Default wurde deshalb auf
4 GiB reduziert.

Der bounded Smoke vom 23. August 2026 ergab:

- 7.933 MiB Peak-VRAM;
- 1.945 MiB verbleibenden VRAM nach Readiness;
- 2.421 residente KAT-Expert-Blöcke im 4-GiB-Tier;
- 42 KAT-Completion-Tokens in 4,365 Sekunden Ende-zu-Ende;
- gleichzeitig erfolgreiche LFM-Generierung und Needle-CPU-Kandidat.

Das ist ein Parallel-Smoke und kein Langzeit- oder Long-Context-Soak. Für eine
Produktionsfreigabe fehlen weiterhin wiederholte 16k-/32k-Prefill-Last,
p50/p95/p99, OOM-Injection, Crash/Restart und längere Speicherbeobachtung.

## Diagnose und Recovery

Runtime-Dateien liegen unter `data/local-model-runtime/` und werden nicht
committet:

```text
data/local-model-runtime/kat.pid
data/local-model-runtime/kat.log
```

Nützliche Prüfungen:

```bash
scripts/local-multi-model-runtime.sh status
nvidia-smi
tail -n 100 data/local-model-runtime/kat.log
curl --fail -H "Authorization: Bearer $ANANTA_LOCAL_MODEL_API_KEY" \
  http://127.0.0.1:8082/v1/models
```

Typische Fehlerbilder:

| Symptom | Wahrscheinliche Ursache | Maßnahme |
|---|---|---|
| Preflight meldet zu wenig VRAM | Expert-Budget plus LFM plus Reserve passen nicht | Erst LFM-Kontext auf 16k reduzieren, danach KAT-Budget senken |
| `qwen36` oder `coli` fehlt | Colibrì nicht oder am falschen Ort gebaut | Pfade prüfen beziehungsweise mit CUDA neu bauen |
| Startfehler bei CUDA | Treiber, Toolkit, Architektur oder Library-Pfad unpassend | `nvidia-smi`, CUDA-Build und `LD_LIBRARY_PATH` prüfen |
| Server bereit, Ausgabe aber fehlerhaft | falscher/alter gs64-CUDA-Backendstand | Branch/Commit und grouped-INT4-Fix prüfen; CPU-/GPU-A/B wiederholen |
| Schlechte Geschwindigkeit nach Neustart | kaltes oder unpassendes Heat-Profil | `HEAT_FILE` prüfen und Warmstart/Hit-Rate beobachten |
| Container erreicht Port 8082 nicht | Host-Bindung oder Bridge-Adresse falsch | Bind-Adresse und `host.docker.internal` prüfen |
| HTTP 401 | API-Key fehlt oder stimmt nicht überein | Host- und Compose-Umgebung konsistent setzen |
| Raw Completion schreibt nach dem Code weiter | Chat-Template/EOS nicht passend angewendet | OpenAI-Chatpfad und Stop-/EOS-Verhalten prüfen |

Bei Speicherdruck gilt die sichere Reihenfolge:

1. laufende Modellgruppe kontrolliert stoppen;
2. `ANANTA_LFM_CTX=16384` setzen;
3. Preflight erneut ausführen;
4. nur wenn nötig `ANANTA_KAT_EXPERT_GB` weiter reduzieren;
5. Readiness, VRAM-Reserve und einen kurzen Parallel-Smoke wiederholen.

Nicht während eines laufenden Prozesses unkoordiniert Expert-Budgets oder
Modellpfade wechseln. Das Operator-Skript behandelt die drei Runtimes als eine
ressourcengeprüfte Gruppe.

## Dauerbetrieb

Für einen vom Terminal unabhängigen Betrieb existiert die User-Unit
[`ananta-local-model-runtime.service`](../../deploy/systemd/ananta-local-model-runtime.service).
Nach Installation der Unit und aktiviertem User-Lingering kann sie die gesamte
Modellgruppe überwachen:

```bash
systemctl --user enable --now ananta-local-model-runtime.service
systemctl --user status ananta-local-model-runtime.service
journalctl --user -u ananta-local-model-runtime.service -n 100
```

Die Unit startet keine alternative Orchestrierung. Sie hält nur die lokalen
Providerprozesse am Leben; Modellwahl und Aufgabensteuerung bleiben beim Hub.

## Sicherheits- und Wartungsregeln

- KAT ausschließlich als lokalen Provider behandeln; Cloud-Fallback ist in
  diesem Profil nicht erlaubt.
- API-Keys nie in Markdown, Git, Shell-History-Ausgaben oder Runtime-Logs
  übernehmen.
- Bei Nicht-Loopback-Bindung immer Authentifizierung und Host-Beschränkung
  aktiv lassen.
- Modellshards, Heat-Datei, Engine-Revision und Konfiguration gemeinsam
  versionieren beziehungsweise hashbinden; sie bilden eine Runtime-Einheit.
- Nach Colibrì-, CUDA-, Treiber- oder Modelländerungen Kerneltest,
  CPU-/GPU-Korrektheitsvergleich, Readiness und Benchmark wiederholen.
- Die 12,14 Tokens/s nicht mit dem 4-GiB-Parallelbetrieb vermischen.
- Keine angeblichen `SRC_*`- oder `RUN_*`-Belege erfinden. Die genannten Werte
  sind lokale Messnotizen vom 23. August 2026, solange keine registrierte
  Run-Evidenz vorliegt.

## Relevante Dateien im Ananta-Repository

- [`scripts/local-multi-model-runtime.sh`](../../scripts/local-multi-model-runtime.sh) – Preflight und Runtime-Lifecycle;
- [`local-kat-lfm-needle-rtx3080.model_profiles.yaml`](../../config/models/local-kat-lfm-needle-rtx3080.model_profiles.yaml) – KAT-Providerprofil;
- [`local-kat-lfm-needle-rtx3080.model_routing.json`](../../config/models/local-kat-lfm-needle-rtx3080.model_routing.json) – Hub-Routingregeln;
- [`compose.local-kat-lfm-needle.yml`](../../docker/compose-next/compose.local-kat-lfm-needle.yml) – additive Containerverdrahtung;
- [`ananta-local-model-runtime.service`](../../deploy/systemd/ananta-local-model-runtime.service) – überwachte Host-Runtime;
- [`todo.ananta-local-multi-model-runtime-and-automated-needle-training.json`](../../todos/todo.ananta-local-multi-model-runtime-and-automated-needle-training.json) – Status, offene Gates und Messnotizen.
