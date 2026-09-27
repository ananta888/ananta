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

Ein Branch: `vision-decision` im Fork `ananta888/llama.cpp-vision-decision`, auf PrismML-Basis (siehe „Branch des Forks“
unten). Die Angaben in diesem Dokument vor dem 2026-09-27 nennen ihn noch `bonsai-decision`.

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

**CUDA-Smoke** (2026-09-26, nach WSL-Neustart, RTX 5060 Ti, Bonsai 27B PQ2_0): alle Felder gültig in 1 Runde,
0,59 s (Prefill 0,41 s, Scoring 0,19 s), typisierter Fehler, Chat erreichbar (Antworttext leer: das Modell verbraucht
die 8 Test-Tokens für Reasoning). `urgency` kam mit p = 0,51: bei `min_probability` 0,8 ein Abstain-Fall.

**Betriebshinweis:** Windows Modern Standby (Netzbetrieb nach 10 min) trennte die eGPU (USB4) kurz; WSL verlor
dabei den GPU-Zugang (`dxgk … Ioctl failed: -19`), der llama-server hing bzw. lief danach nur auf der CPU. Abhilfe:
`powercfg /change standby-timeout-ac 0` (gesetzt 2026-09-26); nach einem Ausfall `wsl --shutdown`, dann das lokale
Wiederherstellungs-Skript `data/meet-media/recover-after-wsl-restart.sh`.

## Tool-Auswahl über `/v1/decision` (JEVCPP-004…006, Etappe 2)

Der schnelle Pfad ist ein **Adapter im vorhandenen Tiny-Tool-Router** (`agent/services/tiny_router/parallel_decision.py`,
`adapter_id` `parallel_decision`), keine neue Steuerlogik. Der Router filtert die Tools vorher deterministisch
(Allowlist, Risikoklassen, `read_only`), der Adapter bewertet nur, was danach übrig ist, und der `CandidateValidator`
prüft das Ergebnis wie bei jedem anderen Tiny-Modell.

**Schema:** Feld `tool` = Enum der erlaubten Tool-Namen + `none`. Jedes Argument mit festem Wertebereich (Enum ≤ 255,
Boolean, Integer mit min/max/Schritt) wird ein Feld `<tool>__<arg>`, das nur gelesen wird, wenn das Tool gewählt ist.

**Ergebnis → Router:**

| Decision | Payload | Router |
|---|---|---|
| Tool sicher (≥ `min_confidence`), alle Argumente fest und sicher | ein `tool_call` | `candidate` |
| `none` sicher | `type: respond` | `respond` (Antwort ohne Tool) |
| Tool unsicher oder Abstain | leer, `tool_uncertain` | `escalate` → normaler Chat-Tool-Call |
| Tool braucht Freitext (z. B. `repo.search.query`) | leer, `free_text_arguments` + `tool_hint` | `escalate` |
| Argument unsicher | leer, `argument_uncertain` | `escalate` |
| ungültige Antwort, Fehler, Timeout | Ausnahme | `escalate`; nach 3 Fehlern 60 s Circuit offen |

**Profil:** `bonsai2-27b-parallel-decision` in `config/models/tiny_action_model_profiles.v1.json`, standardmäßig aus.
Endpunkt über `ANANTA_PARALLEL_DECISION_URL` (nur http/https). Jeder Request sendet `cache_context: false`.

**Live-Probe** (2026-09-26, Bonsai 27B auf der RTX 5060 Ti): „Wie geht es dem Hub?“ → `hub.status` (0,55 s kalt),
„Starte den Companion neu“ → `service.restart {service: companion}`, „Wo wird der CircuitBreaker definiert?“ →
`free_text_arguments` (Hinweis `repo.search`), „Erzähl einen Witz“ → `respond`; warm je 0,18–0,19 s.

**Freitext:** siehe Abschnitt „Freitext-Argumente“ unten (Profil-Flag `open_field`). Der
Hub-Tool-Loop (`ananta_worker_tool_loop`) ist auf den Workern aus; `tiny_router.mode: shadow` sammelt dort also
nichts, solange der Loop aus ist. Echte Tool-Calls macht derzeit der Meet-Companion, darum läuft der Shadow dort.

## Upstream-Port ad129b08d (Hybrid-Modelle in einem Pass)

Der Upstream-Branch ist auf einen neueren llama.cpp-Master umgebaut; ein Merge brächte Hunderte fremder Commits. Portiert
ist nur `ad129b08d`, angepasst an das Trie-/Kept-Path-`score_branches` des Forks (`bonsai-decision` @ `a53a1e78e`): auf
rekurrenten/hybriden Modellen (dort ohne Token-Sharing) laufen die Äste längste zuerst und werden auf die Länge des
längsten Asts ihrer Gruppe aufgefüllt; die Logits werden am letzten echten Token gelesen.

| Messung (RTX 5060 Ti, Bonsai 27B) | vorher | nachher |
|---|---|---|
| Tool-Wahl, 8 Tools/13 Felder, warm Median (48 Prompts) | 0,544 s | 0,267 s |
| Smoke: Scoring | 0,19 s | 0,086 s |
| geänderte Gewinner / max. Abweichung p | – | 0 / 0,012 |

`llama-parallel-decision` (CLI) baute auf der PrismML-Basis nicht (`llm_add_n_cpu_ffn_overrides` fehlt dort); seit `7cae572ea`
baut es wieder (die `--n-cpu-moe`-Logik steht inline). Achtung beim Neubau: auch die Shared Libraries werden neu
gelinkt, eine Kopie nur von `llama-server` ist kein Rollback; Rollback = Checkout des vorigen Commits und inkrementell bauen.

## Kalibrierung und Companion-Shadow (JEVCPP-009)

- **Set:** `benchmarks/tiny_tool_router/decision_calibration.v1.json`, 48 DE/EN-Fragen über das Companion-Toolset
  (synthetisch, nie Release-Evidenz). Lauf: `python3 scripts/jev_tool_decision_calibration.py --url http://127.0.0.1:18150`.
- **Auswertung** (`agent/services/tiny_router/decision_calibration.py`): Precision/Coverage je Schwelle, ECE, Konfusion.
  Eine Schwelle wird nur empfohlen, wenn die einseitige 95-%-Untergrenze der Precision das Ziel (0,95) erreicht.
- **Ergebnis 2026-09-27:** 48/48 richtig, ECE 0,027. Ohne einen einzigen Fehler trennt das Set keine Schwellen
  (Untergrenze 0,947 < 0,95), also keine Empfehlung: `min_confidence` bleibt 0,8.
- **Companion-Shadow** (`worker/meet_media/tool_decision_shadow.py`, an bei `MEET_TOOL_DECISION_SHADOW_URL`): Nach
  jeder Antwort wird die Frage ein zweites Mal per `/v1/decision` bewertet, in einem Daemon-Thread, ohne Einfluss auf
  Antwort oder Timing. Protokoll `/state/tool-decision-shadow.jsonl` mit Route, erzwungenen und eigenen Calls,
  `actual_first` (erster wirklich gelaufener Call) und Decision; ohne Fragetext (nur Hash und Länge). Auswertung:
  `python3 scripts/jev_tool_decision_calibration.py --from-shadow data/meet-media/worker-state/tool-decision-shadow.jsonl`.
  Gemessen: 0,16 s je Shadow-Entscheidung.

## Freitext-Argumente (Upstream-PR #14, portiert)

Der Kern bewertet nur geschlossene Felder. PR #14 (thecodacus/llama.cpp, akudo7, offen) ergänzt **ein begrenztes
offenes Feld** (`{"type":"string","max_tokens":N}`, 1–1024): Es wird im selben Aufruf nach dem Scoring auf dem Trunk
erzeugt, mit den gewählten geschlossenen Werten davor. Der Text sieht also das gewählte Tool; die Feldabhängigkeit ist
gelöst statt verboten. Portiert auf `bonsai-decision` (`0f9f9ff10`) plus zwei eigene Ergänzungen:

- **Stopp am Wertende** (`c2a4426ce`): Das Modell schließt den String und erfindet danach weitere Felder bis
  `max_tokens`. Die Erzeugung stoppt am ersten unmaskierten `"` (sonst an Komma, Zeilenende oder Klammer).
  Erzeugung 550–800 ms → 63–235 ms.
- **`when`** (`b8d5823a3`): `"when": {"tool": [...]}` erzeugt das Feld nur, wenn das geschlossene Feld einen der Werte
  gewonnen hat, sonst `"skipped": true` ohne Kosten. Spart ~155 ms bei jeder Antwort ohne Tool.

**Adapter:** Mit `metadata.open_field: true` im Profil bekommt ein Tool, dessen einziges freies Argument ein
Pflicht-String ist (`query`, `handle`), dieses aus dem Feld `text_argument` (48 Tokens, `when` = diese Tools). Geprüft:
String, vollständig, nicht leer, höchstens 200 Zeichen, keine Steuerzeichen; sonst `text_argument_invalid` → Chat-Pfad.
Tools mit mehr Freitext (z. B. zwei Manifeste) eskalieren weiter. Der Payload markiert erzeugte Argumente
(`generated_arguments`), weil sie keine Wahrscheinlichkeit haben; der `CandidateValidator` prüft sie gegen das
Tool-Schema.

**Messung** (2026-09-27, RTX 5060 Ti, Bonsai 27B, 48 Kalibrier-Prompts, 8 Companion-Tools): 48/48 Tools richtig,
**24/24 erzeugte Argumente passend** (`CircuitBreaker`, `build_tool_decision_schema`, `hac:…`-Handles, Architekturfragen).
Warm-Median eines vollständigen Tool-Calls mit Text 0,625 s, ohne Text 0,268 s.

**Companion-Shadow:** Er erzeugt das Argument mit (`MEET_TOOL_DECISION_SHADOW_ARGUMENT`, Standard an) und protokolliert
nur, ob es dem Argument des wirklich gelaufenen Calls entspricht (`argument_equal`) und wie lang es ist, ohne Text.
Erster End-to-End-Lauf: „CircuitBreaker“ gleich dem vom Router erzwungenen Suchbegriff. Bei „Welche Index-Layer hat
CodeCompass?“ erzwang der Router eine Suche, das Modell rief danach selbst `codecompass_layers_heads`, und der
Jev-Modus wählte `layers_heads` direkt. `actual_first` ist deshalb nicht immer das bessere Label; die Auswertung soll
`model_first` mitlesen.

- **Immer ein String** (`d6a9bdd8f`): Das Präfix enthält das öffnende `"`; das Modell schreibt nur den Inhalt bis
  zum schließenden `"`. Vorher begann Bonsai einmal mit einer Zahl (`8` statt `passwort`), und die ging als Wert durch.

**Tests im Fork:** `tools/server/tests/unit/test_decision.py` hat 10 Tests für das offene Feld (Antwortform, Erzeugung
ändert das Scoring nicht, `when`, Bilder, Kontext-Cache, typisierte Fehler); 34/34 grün auf `bonsai-decision` (CUDA)
und `vision-decision` (CPU). Lauf: `LLAMA_SERVER_BIN_PATH=…/llama-server PORT=18160 python -m pytest unit/test_decision.py`
in `tools/server/tests` (eigenes venv mit `requirements.txt` und `filelock`).

## Upstream-Abgleich (PR #14)

Stand 2026-09-27: PR #14 offen, ein Commit (`d0ec6f954`), kein Review. Unsere Ports liegen auf `vision-decision` (seit
2026-09-27 der einzige Branch, PrismML-Basis). Abweichungen vom PR, die bei einem Upstream-Merge abzugleichen sind:

| Punkt | PR #14 | unser Port |
|---|---|---|
| Stopp | bis EOS oder `max_tokens` | am schließenden `"` (Modell erfindet sonst Felder) |
| Wertform | beliebiger JSON-Wert, nachträglich geparst | Präfix mit `"`: immer String |
| Prefix-Batch | `min(n_batch, len)` angelegt, alle Tokens hinein (Überlauf bei langem Präfix) | in `n_batch`-Stücken |
| Bedingung | – | `when` + `skipped` |
| Schema | nur offenes Feld erlaubt | mindestens ein geschlossenes Feld; offenes Feld nicht `nullable` |
| Startposition | `shared + context` | `pos_next` des Trunks (M-RoPE-Bilder, `context_tail`) |
| API | `max_tokens`, `open_sampling`, `open_temp`, `generated_tokens`, `generation_ms` | gleich, plus `skipped` |

Kein Beitrag an den PR (Entscheidung 2026-09-27); die Korrekturen bleiben im eigenen Fork.

## Kalibrierung mit Grenzfällen und Vergleich mit dem Chat-Pfad (JEVCPP-009)

Zweites Set `benchmarks/tiny_tool_router/decision_calibration_hard.v1.json` (71 Fälle): Allgemeinwissen, das technisch
klingt; Meeting-Fragen ohne passendes Tool; umgangssprachliche und vertippte Code-Fragen; Grenzen Suche/Überblick/
rekursive Analyse; Handles; Mehrfachabsichten; Injection-Versuche. `expected.also_acceptable` nennt vertretbare
Alternativen bei echter Mehrdeutigkeit. Split: `sha256(case_id)` mod 5 = Holdout (27 von 119), die Schwelle wird nur
auf Validation gewählt. Lauf: `scripts/jev_tool_decision_calibration.py --cases … --cases … --compare-chat`.

| 119 Fälle, Bonsai 27B | Decision-Modus | Chat-Tool-Call (gleiches Modell) |
|---|---|---|
| Tool richtig (mit Alternativen) | 119/119 | 118/119 („Wer ist gerade im Raum?“ → Suche) |
| Tool richtig, nur Label (Validation) | 93,5 % | – |
| erzeugte Argumente passend | 59/59 | 98,3 % |
| Latenz Median / p90 | 0,37 s / 0,67 s | 1,19 s / 1,90 s |

- Die 7 Abweichungen vom Label im harten Set sind alle vertretbare Alternativen und liegen alle bei p 0,46–0,84:
  Die Wahrscheinlichkeit trennt Mehrdeutigkeit ab.
- **Schwelle 0,85** (auf Validation mit strengen Labels gewählt): Holdout 21/21 richtig, Abdeckung 78 %
  (95-%-Untergrenze 0,886, kleines n). Im Profil gesetzt (`min_confidence` 0,85).
- **Kombiniert** (≥ 0,85 schneller Pfad, sonst Chat): 97 Fälle schnell, alle richtig, 22 an den Chat, alle richtig;
  mittlere Latenz 0,66 s statt 1,19 s. Bei 0,80 wären 3 Tool-Wahlen falsch.
- Grenzen: synthetische Labels eines Annotators, kleiner Holdout. Der Companion-Shadow bestätigt oder korrigiert die
  Schwelle mit echten Fragen (`--from-shadow`, ebenfalls mit Split).

## Branch des Forks: `vision-decision` auf PrismML-Basis

Der Fork `ananta888/llama.cpp-vision-decision` hat **einen** Branch: `vision-decision` (Default, in Ananta gepinnt als
`vendor/llama.cpp-vision-decision`, und derselbe Stand läuft als produktiver llama-server).

```
ggml-org/llama.cpp ── PrismML-Eng/llama.cpp (prism) ── vision-decision   (Basis)
thecodacus/llama.cpp (parallel-decision) ──────────────┘                 (/v1/decision portiert: e33bf977a)
```

**Warum PrismML:** Das Produktivmodell Bonsai 2 27B liegt im ternären Format `PQ2_0`; die Kernels dafür (und für `PTQ1_0`,
dazu die Hadamard-Rotation) gibt es nur im PrismML-Fork, normales llama.cpp lädt die Datei nicht. PrismML ergänzt nur:
normale GGUF-Modelle laufen unverändert, auch mit Bildern (Server-Tests 52/52: `test_decision.py`, `test_vision_api.py`).
Preis: Die mainline-Basis ist so alt wie PrismMLs letzter Abgleich (2026-08-25), nicht die neueste.

**Zusammenführung am 2026-09-27:** Bis dahin gab es zwei Linien mit gleichem Decision-Code, `vision-decision` auf
thecodacus/mainline-Basis (Stand 2026-09-19) und `bonsai-decision` auf PrismML-Basis. Weil nur die PrismML-Linie
produktiv lief und jede Änderung doppelt gepflegt werden musste, wurde `bonsai-decision` in `vision-decision` umbenannt
(GitHub leitet den alten Namen weiter). Die alte mainline-Linie liegt als Tag `archive/vision-decision-mainline`
(`7d6d1b396`); darüber bleiben die Submodul-Pins älterer Ananta-Commits erreichbar, und die Linie lässt sich jederzeit
als Branch wiederherstellen. Kein Merge-Commit mit „ours“: Git hielte die ~350 neueren mainline-Commits sonst für
enthalten, und spätere Upstream-Abgleiche verlören sie still. Neu bewerten, wenn PrismMLs Kernels in mainline ankommen
(zurück auf mainline-Basis) oder ein Modell nur mit neuester mainline läuft (dann PrismML gezielt auf mainline ziehen).

**Pflege:** Basis-Abgleich `git fetch prismml && git merge prismml/prism`; Änderungen am Decision-Prototyp von
thecodacus werden von Hand portiert (andere Basis). Details stehen in `AGENT.md` des Forks.

**Arbeitsregeln im Fork** (seine `AGENTS.md`/`AGENT.md`): Commit und Push nur nach ausdrücklicher Freigabe je Aktion,
`Assisted-by:` statt `Co-authored-by:`, nur ASCII, keine PRs oder Kommentare upstream. Die Commits vom 2026-09-26/27 vor
der Zusammenführung tragen noch `Co-Authored-By`; sie bleiben so, weil Ananta-Submodul-Pins auf sie zeigen.

**Lokal:**

| Verzeichnis | Stand | Builds |
|---|---|---|
| `~/llama.cpp-bonsai-decision` (Worktree) | Branch `vision-decision` | `build-cuda`: der laufende Server (Port 18150); Verzeichnisname aus der Zeit vor der Zusammenführung |
| `~/llama.cpp-vision-decision` (Haupt-Checkout mit dem `.git`) | Tag `archive/vision-decision-mainline` (detached) | `build`, `build-cuda` der alten Linie |
| `vendor/llama.cpp-vision-decision` (Submodul, eigener Klon) | Branch `vision-decision` | `build-cpu` (CPU-Testläufe) |

## Produktiv: Companion und Worker (2026-09-27)

**Gemeinsamer Vertrag.** Schema-Aufbau, Request-Body und strikte Auswertung liegen einmal in
`ananta_contracts/tool_decision.py` (beide Images enthalten das Paket; das Meet-Image darf `agent` nicht importieren).
Das Ergebnis ist eine `ToolDecision` (`call` / `respond` / `abstain`). Der Hub-Adapter
(`agent/services/tiny_router/parallel_decision.py`) behält nur Laufzeit, Circuit Breaker und Adapter-Vertrag; der
Companion (`worker/meet_media/tool_decision.py`) nur seinen gehärteten Transport (`BoundedJsonClient`) und die Settings.
Vorher gab es Schema und Auswertung doppelt.

**Regeln des Vertrags:**
- Nur **Pflicht**-Argumente mit festen Werten werden bewertet; optionale behalten den Tool-Standard (ein optionales
  `limit` drückte sonst eine klare Suche auf p = 0,49).
- Textargument (offenes Feld): der eine Pflicht-String; bei Tools ohne Pflicht-Argumente (die MCP-Tools des Hubs
  deklarieren nichts als Pflicht) der **führende** Parameter, wenn er ein freier String ist (`query`, `pattern`,
  `question`, `handle`), leer = ohne ihn. Ist der führende Parameter etwas anderes (Liste, Objekt), entscheidet das Modell.
- Konfidenz = Minimum aus Tool und bewerteten Pflicht-Argumenten; darauf ist kalibriert: **Schwelle 0,90** (streng auf
  Validation gewählt, Holdout 20/20, Abdeckung 74 %).

**Companion (aktiv).** `MEET_TOOL_DECISION_URL` + `MEET_TOOL_DECISION_FAST=1` (lokales Overlay
`data/meet-media/windows-ollama.yml`, Wiederherstellungs-Skript): Vor dem Modellaufruf fragt der Companion
`/v1/decision`; ein Tool-Call ab 0,90 (`MEET_TOOL_DECISION_MIN_CONFIDENCE` darf nur erhöhen) wird wie die Router-Suche
erzwungen, außer der Router hat dieses Tool schon erzwungen. Darunter entscheidet das Modell wie bisher. Der Shadow
protokolliert dieselbe Entscheidung (`mode: fast`), ohne zweiten Aufruf. Das Meet-Image mountet dafür jetzt das ganze
`ananta_contracts` aus dem Repo (vorher nur eine Datei). Gemessen: „Welche Index-Layer …“ 2,9 s statt 4,4 s
(`codecompass_layers_heads` direkt, p = 1,00); Smalltalk ohne Tool.

**Worker (aktiviert).** Über die Hub-Route `POST /config` (Admin, Operation-Gate, Audit): `ananta_worker_tool_loop.enabled
= true`, `tiny_router.mode = active`, `profile_order = [bonsai2-27b-parallel-decision]`, `top_k = 16` (der Jev-Modus
bewertet 16 Tools auf einmal; 5 schnitten `codecompass.search`/`repo.grep` weg). Endpunkt:
`ANANTA_PARALLEL_DECISION_URL=http://host.docker.internal:18150` im lokalen Overlay `data/meet-media/compose-local-runtime.yml`,
Worker neu erstellt (sonst unveränderte Umgebung). Im Worker gemessen (16 Tools): `git.status` 0,4–0,9 s,
`repo.grep {pattern}` 0,3 s, `codecompass.architecture_overview {query}` 1,1 s direkt; mehrdeutige Fragen
(Suche vs. grep vs. Symbolsuche) und Schreib-Tools gehen ans Modell.

**Offen, nicht vom Jev-Modus verursacht:**
- `codecompass.analytics_query` scheitert im Hub mit `VectorStoreError`.
- `codecompass.layers_heads` ohne `profile_id` liefert `"head": null`; der Companion folgert daraus fälschlich „kein
  aktiver Head“.

## Standardmodell von Ananta: der eGPU-llama-server (2026-09-27)

Hub und Worker nutzen als Standard denselben llama-server wie der Companion und der Jev-Modus (Bonsai 2 27B auf der
eGPU, Port 18150). Gesetzt einmal zentral über die Hub-Route `POST /config` (landet in der Config-DB, Hub und Worker
lesen sie):

| Schlüssel | Wert |
|---|---|
| `local_openai_backends` | `[{id: "llamacpp", name: "eGPU llama.cpp (Bonsai 2 27B, Jev decision mode)", base_url: "http://host.docker.internal:18150/v1", models: [<Modellpfad>]}]` |
| `default_provider` | `llamacpp` |
| `default_model` | `/mnt/d/Bonsai-demo/models/bonsai2-gguf/27B/Ternary-Bonsai-2-27B-PQ2_0.gguf` (die Modell-ID, die `/v1/models` meldet) |
| `llm_config` | `{provider: llamacpp, model: <wie oben>, base_url: <wie oben>}` |

Vorher war das Standardmodell doppelt und verschieden gesetzt: der Hub über Umgebungsvariablen (`DEFAULT_PROVIDER=openai`,
`OPENAI_URL` auf den eGPU-Server; die Endpunkt-Richtlinie blockierte das als „externes OpenAI“), die Worker auf
LM Studio (ohne geladenes Modell). Die DB-Konfiguration überstimmt beides.

Nötige Korrekturen dafür (Tests je daneben):
- Die ID `llamacpp` ist ein in der Endpunkt-Richtlinie bekannter lokaler Provider; ein Eintrag mit dieser ID nutzt jetzt
  auch den Transport `llamacpp` (vorher immer `openai`, was jede lokale Adresse als extern blockierte).
- Die OpenAI-Strategie ruft die URL auf, die die Richtlinie geprüft hat (`/v1` → `/v1/chat/completions`), statt 404.
- `sgpt` (Backend `ananta-worker`) löst einen Standard-Provider aus `local_openai_backends` auf und rendert keine
  Terminal-Markdown mehr (der Zeilenumbruch bei 80 Spalten zerstörte das Tool-Loop-JSON ab der zweiten Antwort).

Server-Start (`data/meet-media/recover-after-wsl-restart.sh`): `-np 2` (Companion und Worker blockieren sich nicht,
gleicher Gesamtkontext) und `--reasoning off` (Bonsai denkt sonst sein Token-Budget leer; `sgpt` kann das nicht pro
Anfrage abschalten).

Geprüft: Hub `POST /llm/generate` → „bereit“ über `llamacpp`; Worker-Tool-Loop mit echtem Modell: `repo.grep` →
`repo.read_file_range` → richtige Antwort in 5,5 s.
