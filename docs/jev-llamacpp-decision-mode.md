# Jev-Modus für llama.cpp: Quellen, Stand und Abgrenzung (JEVCPP-001)

Stand: 2026-09-26 · Track: `todos/active/todo.jev-llamacpp-parallel-decision-mode.json`

„Jev-Modus“ meint hier einen **Inferenzmodus für vorhandene Decoder-Modelle**: Statt ein JSON-Objekt Token für
Token zu erzeugen, werden pro Feld nur die erlaubten Werte bewertet, alle Felder parallel aus demselben KV-Cache.
Ananta setzt ihn bereits ein, als Fork mit Vision-Erweiterung (`vendor/llama.cpp-vision-decision`,
Track `todo.vision-parallel-decision-llamacpp.json`).

## Begriffe und Abgrenzung

| Name | Was es ist | Gewichte | Verhältnis zu Ananta |
|---|---|---|---|
| **Jev** (TypeSafe) | proprietäres Entscheidungsmodell mit „System One“-API | eigene, proprietär | nicht verwendet, nicht nachgebaut (Track-Regel) |
| **parallel-decision** (thecodacus) | offener llama.cpp-Branch, `POST /v1/decision` für beliebige GGUF-Decoder | keine neuen, jedes Modell | Upstream unseres Forks |
| **llama.cpp-vision-decision** (ananta888) | unser Fork: + Bilder (libmtmd), Kalibrierung, Kontext-Cache, Playground | keine neuen | läuft lokal (`llama-server --decision-seqs`) |
| **system-one** (llama.cpp PR #29321) | Bibliothek + CLI für das System-One-Format; liest Labels aus GGUF-Keys | für darauf trainierte Modelle | nicht verwendet, beobachten |
| **Jeff** (GLiFormer) | separates Encoder-Modell, Jev-kompatible API | eigenes Modell (~400M) | eigener Track `todo.jeff-local-specialist-gate-adapter.json` |

Der Unterschied Jeff ↔ llama.cpp-Ansatz: Jeff ist **ein zusätzliches Modell** (Encoder, Klassifikation);
parallel-decision ist **ein anderer Decodier-Pfad** für die Modelle, die ohnehin laufen.

## Quellen (verifiziert)

| Quelle | Stand | Lizenz |
|---|---|---|
| `thecodacus/llama.cpp`, Branch `parallel-decision` | Kopf `ad129b08d` (2026-09-25); Prototyp = 3 Commits: `b9244f893` „server : add /v1/decision“ (2026-09-20), `14d04e755` README, `ad129b08d` Hybrid-Modelle in einem ubatch | MIT (llama.cpp/ggml, `LICENSE` im Baum; GitHub erkennt beim Fork keine) |
| `thecodacus/decision-playground` | Browser-Playground (2026-09-19) | **keine Lizenz** → Code nicht übernehmen |
| `ananta888/llama.cpp-vision-decision`, Branch `vision-decision` | Submodul-Pin `34d778a3e`; 43 eigene Commits auf dem Prototyp; Merge-Basis `14d04e755` | MIT |
| llama.cpp (ggml-org) | **kein** PR/Issue von thecodacus; verwandt, offen: #29321 „system-one : typed decision readout“ (2026-09-23), #29363 „laya“-Entscheidungsmodell (2026-09-24) | MIT |

Offene PRs am Upstream-Branch, nicht in unserem Fork: #13 exakte Baumverteilungen, #14 hybride Entscheidung
(geschlossene Felder + ein begrenztes offenes Textfeld in einem Aufruf), #15 kein `n_seq_decision` im MTP-Kontext.
Unser Fork hat `ad129b08d` noch nicht (siehe unten).

## API-Vertrag (aus Code und README, nicht aus Video-Aussagen)

`POST /v1/decision` (`tools/parallel-decision/README.md`, `tools/server/`):

- **Request:** `schema` (Felder `enum` 1–255 Werte, `boolean`, `integer` min/max/step, `number` mit Raster; `nullable`),
  `contexts` (1–256 Texte, bei uns auch Bilder), `instructions`, Optionen `mode` (`tree`/`greedy`/`auto`),
  `cache_prompt`, `share_tokens`, `layout` (`schema_first`/`context_first`), `temperature`, `abstain`,
  `return_probs`, `trace`.
- **Response:** pro Kontext `decision` (nach Schema zusammengesetzt, immer gültig) und je Feld `value`,
  `probability`, bei Baum-Feldern `margin` und `entropy`, `abstain`; dazu `usage` und `timings`.
- **Scoring:** Die erlaubten Werte eines Felds bilden einen Token-Trie. Die Äste zweigen per
  `llama_memory_seq_cp` vom gecachten Präfix ab und werden in **einem** `llama_decode` bewertet. Felder sehen
  einander nicht. `tree` bewertet jeden Verzweigungsknoten (exakte Wahrscheinlichkeit auch bei Mehrtoken-Werten
  mit gemeinsamem Präfix), `greedy` läuft den Trie entlang.
- **Confidence:** Rohwahrscheinlichkeit ≠ Trefferwahrscheinlichkeit. Temperatur und `abstain`
  (`min_probability`, `min_margin`) pro Feld und Modell, kalibriert mit `bench/evaluate.py` auf gelabelten Daten.

## Was belegt, was experimentell, was offen ist

**Belegt** (Code und Tests im Fork, lokal gelaufen):
- keine neuen Gewichte, anderer Inferenzpfad; Chat-Endpunkte unverändert (Fork-Tests `test_decision.py`, `test_basic`);
- parallele, voneinander isolierte Felder aus gemeinsamem Präfix-Cache; Mehrtoken- und Shared-Prefix-Kandidaten
  über den Trie (Vision-Track VDEC-002/004, 23 von 27 Aufgaben erledigt);
- Schema-Gültigkeit garantiert, inhaltliche Richtigkeit nicht (README „Calibration and abstain“, SmolVLM-Beispiel);
- abhängige Felder passen nicht in diesen Modus (Felder sehen einander nicht); dafür Abstain und Eskalation.

**Experimentell:**
- Performance-Zahlen der README stammen von fremder Hardware (RTX 3060, 2×3090). Das eigene Profil auf der
  RTX 5060 Ti ist offen (VDEC-014, blockiert);
- Hybrid-/rekurrente Modelle: `share_tokens` ist dort aus; `ad129b08d` (Äste auf gleiche Länge auffüllen, 1 statt 3
  ubatches) fehlt uns noch;
- Kalibrierung und Abstain-Schwellen für Ananta-Anwendungsfälle (VDEC-015/016 teilweise).

**Offen oder nicht aktuell:**
- Upstream-Zukunft: thecodacus hat nichts an llama.cpp eingereicht; dort läuft mit #29321 ein anderer Ansatz
  (System-One-Format, spezielle Modelle). Unser Endpunkt bleibt ein Fork-Feature;
- das Playground-Repo ist unlizenziert; wir nutzen nur den Playground im Fork.

## Folgerungen für den Jev-Track

- **JEVCPP-002** (reproduzierbarer CUDA-/CPU-Build): Der CUDA-Build läuft (`build-cuda`, `b10743`,
  `--decision-seqs 12`). Offen sind festgehaltene Build-Flags und Compiler, ein CPU-Build und der Smoke-Test als Skript.
- **JEVCPP-003** (Scoring und Token-Grenzen): Die Fork-Tests decken Trie und Shared Prefix ab. Zu prüfen ist, ob
  Whitespace-, Quote-, Colon- und Chat-Template-Effekte explizit getestet sind, und ob Rohscores gespeichert werden.
- **Upstream-Sync:** `ad129b08d` übernehmen, bevor die Performance gemessen wird.

## Build und Start (JEVCPP-002)

Zwei Checkouts desselben Repos `ananta888/llama.cpp-vision-decision`:

| Checkout | Branch | Zweck |
|---|---|---|
| `vendor/llama.cpp-vision-decision` (Submodul) | `vision-decision` | Decision + Vision, Referenz für Tests |
| `~/llama.cpp-bonsai-decision` | `bonsai-decision` | + Q2_0/Ternary-Bonsai (PrismML), läuft als Companion-/Hub-Modell |

**CUDA** (RTX 5060 Ti, so gebaut für den laufenden Server, Build `b10743` @ `60dcaa127`):
CUDA 12.9 (`nvcc` 12.9.r12.9), GCC 13.3, CMake 3.28.

```bash
cmake -B build-cuda -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON -DGGML_CUDA_FA=ON -DGGML_NATIVE=ON \
      -DCMAKE_CUDA_ARCHITECTURES=120 -DCMAKE_CUDA_COMPILER=$HOME/cuda-12.9/bin/nvcc
cmake --build build-cuda -j --target llama-server llama-parallel-decision
build-cuda/bin/llama-server --host 0.0.0.0 --port 18150 \
  -m /mnt/d/Bonsai-demo/models/bonsai2-gguf/27B/Ternary-Bonsai-2-27B-PQ2_0.gguf \
  --mmproj /mnt/d/Bonsai-demo/models/bonsai2-gguf/27B/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf \
  -c 65536 -np 1 --decision-seqs 12 --decision-ctx-cache 1 -ngl 99 -fa on --image-max-tokens 1024 \
  --no-warmup -ctk q8_0 -ctv q8_0
```

**CPU** (im Submodul, 2026-09-26 gebaut und geprüft):

```bash
cmake -B build-cpu -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DGGML_NATIVE=ON -DLLAMA_CURL=OFF
cmake --build build-cpu -j 12 --target llama-server llama-parallel-decision
build-cpu/bin/llama-server --host 127.0.0.1 --port 18151 -m ~/models/vlm/Qwen3-VL-2B-Instruct-Q8_0.gguf \
  -c 8192 -np 2 --decision-seqs 16 -t 6
```

**Smoke-Test:** `python3 scripts/jev_decision_smoke.py --url http://127.0.0.1:18151`. Er prüft Enum, Boolean, Integer
und Enum-Kandidaten mit gemeinsamem Präfix (`refund_full`/`refund_partial`/`refund_denied`): jedes Feld genau ein
erlaubter Wert; ein ungültiges Schema ergibt einen typisierten `invalid_request_error`; `/v1/chat/completions` läuft
daneben. Ergebnis CPU (Qwen3-VL-2B Q8_0, 6 Threads): alle Felder gültig in 1 Runde, 1,1 s (Prefill 0,98 s, Scoring
0,13 s), Chat „pong“ in 0,16 s.

**CUDA-Smoke offen:** Am 2026-09-26 um 19:40 verlor WSL den Zugriff auf die eGPU (`dxgk … Ioctl failed: -19`,
danach „GPU access blocked by the operating system“), kurz nach dem Start von Vision-Integrationstests gegen den
27B-Server. Der Server läuft seitdem auf der CPU. Der CUDA-Smoke folgt, sobald die GPU wieder verfügbar ist.
