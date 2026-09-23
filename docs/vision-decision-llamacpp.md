# VisionDecision Specialist (llama.cpp-vision-decision)

Optionaler, standardmaessig abgeschalteter Provider fuer typisierte Bild-Entscheidungen ueber
`POST /v1/decision` aus dem Fork `ananta888/llama.cpp-vision-decision` (Submodule
`vendor/llama.cpp-vision-decision`, Stand `a0ae4ad5e`). Das Bild geht ueber libmtmd/mmproj direkt in den
KV-Kontext; alle Felder werden parallel aus demselben multimodalen Kontext bewertet, ohne Bildbeschreibung
als Zwischentext. API, Modellmatrix und Benchmarks: `vendor/llama.cpp-vision-decision/tools/parallel-decision/README.md`.

- Provider: `agent/services/vision_decision_provider.py` (`VisionDecisionProvider`, `VisionDecisionOutcome`,
  `VisionDecisionSchema`, `get_vision_decision_provider`)
- Hub-Abbildung: `agent/services/vision_decision_hub_gate.py` (`gate_vision_decision`, `VisionEscalation`)
- Tests: `tests/test_vision_decision_provider.py` (gemocktes HTTP), `tests/test_vision_decision_integration.py`
  (echter `llama-server`, sonst SKIPPED)
- Todo-Track: `todos/active/todo.vision-parallel-decision-llamacpp.json` (VDEC-021 ff.)

Noch nicht angebunden: eine Hub-Route oder ein konkreter Konsument. Aufrufer nutzen `get_vision_decision_provider()`
und `gate_vision_decision()` direkt.

## Betrieb

`llama-server` aus dem Fork laeuft als eigener Prozess; Ananta spricht nur HTTP. Fuer den Hub-Container auf dem
Docker-Bridge-Interface binden (nicht auf `0.0.0.0`):

```
cd vendor/llama.cpp-vision-decision
./build/bin/llama-server -m ~/models/vlm/Qwen3-VL-2B-Instruct-Q8_0.gguf \
  --mmproj ~/models/vlm/mmproj-Qwen3-VL-2B-Instruct-Q8_0.gguf \
  -c 8192 --decision-seqs 16 --port 8096 --host 172.17.0.1 -t 6
```

Optional `--api-key` (dann `VISION_DECISION_API_KEY_FILE` setzen), `--decision-max-media N` (Default 16),
`--decision-media-cache MiB` (Default 256, Schluessel: Bild-Hash + Slice).

| Variable | Default | Bedeutung |
|---|---|---|
| `VISION_DECISION_ENABLED` | `false` | Aus: kein Provider, keine weitere Variable gelesen, kein Dienstkontakt |
| `VISION_DECISION_URL` | `http://127.0.0.1:8096` | nur `scheme://host[:port]`; `http` nur fuer Loopback, private IPs, Compose-Namen |
| `VISION_DECISION_API_KEY_FILE` | - | Bearer-Token aus `/run/secrets/...` (optional; >= 16 Zeichen) |
| `VISION_DECISION_API_KEY` | - | Alternative ohne Datei |
| `VISION_DECISION_TIMEOUT_MS` | `30000` | 100..300000; eine optionale Deadline des Aufrufers senkt ihn weiter |
| `VISION_DECISION_MAX_MEDIA` | `4` | 1..16 Bilder pro Request, lokal vor jedem Upload geprueft |
| `VISION_DECISION_MAX_IMAGE_BYTES` | `4194304` | 1 KiB..10 MiB pro Bild (Rohdaten) |
| `VISION_DECISION_MAX_IMAGE_SIDE` | `1024` | laengere Kante wird lokal darauf verkleinert |
| `VISION_DECISION_MODELS` | leer | Modell-Allowlist; Antwort-`model` (oder dessen Basename) muss enthalten sein, sonst `model_not_allowed` |
| `VISION_DECISION_SCHEMAS` | leer | Schema-ID-Allowlist; andere IDs werden lokal abgelehnt (`schema_not_allowed`) |
| `VISION_DECISION_MIN_PROBABILITY` | `0.8` | Abstain-Schwelle, (0, 1] |
| `VISION_DECISION_MIN_MARGIN` | `0.3` | Abstain-Schwelle fuer Top-1 minus Top-2, (0, 1] |

## Ablauf und Regeln

1. Lokal vor dem Upload: Schema (nur `enum`/`boolean`/`integer`/`number`, 1-32 Felder, 1-255 Werte, Beschreibung
   Pflicht; `string` wird abgelehnt), Anzahl Kontexte (1-16) und Bilder (`MAX_MEDIA`), Bytes, Pixel (<= 40 MP),
   Format (PNG/JPEG/WebP, nicht animiert). Jedes Bild wird mit Pillow dekodiert und als PNG neu kodiert (keine
   EXIF-/Metadaten) und nur als `data:`-URI gesendet, nie als URL.
2. Ananta sendet immer `abstain`-Schwellen; Temperaturen pro Feld stehen in der Feld-Spezifikation (das
   Top-Level-`temperature` des Forks ist nur eine Zahl fuer alle Felder).
3. Die Antwort wird gegen das Schema geprueft: Ergebnisanzahl, Feldmenge, jeder Wert erlaubt, `decision` ==
   `fields[*].value`, Wahrscheinlichkeit in [0, 1]. Sonst `bad_response` ohne Wert.
4. Ein Feld gilt als unsicher, wenn der Server es in `abstained` fuehrt, `abstain: true` meldet, das
   `abstain`-Flag fehlt oder Ananta lokal `probability < MIN_PROBABILITY` bzw. `margin < MIN_MARGIN` feststellt.
   Unsichere Felder haben `value = None`; der Top-Wert steht nur als `top_value`/`candidate` fuer das
   Eskalationsziel zur Verfuegung.

| Ergebnis | Kind | Hub-Aktion |
|---|---|---|
| Fehler, Timeout, HTTP 4xx/5xx, kaputte Antwort, Modell nicht erlaubt | `no_decision` | `normal_path`, kein Feld, kein Default-Label |
| Feld unsicher | `escalate` | `escalate` mit `VisionEscalation{target, reasons, probability, margin, entropy}` |
| Feld akzeptiert, keine Policy | `proposal` | `confirm` |
| Feld akzeptiert, Hub-Policy | `proposal` | `allow` -> `act`, `confirm` -> `confirm`, `deny`/Fehler/unbekannt -> `deny` |

Eskalationsziele: `chat_completion` (Default: normaler VLM-Chat mit `json_schema` auf demselben Bild),
`larger_model`, `human` (pro Feld ueber `human_review_fields`).

Fehlercodes (`VisionDecisionOutcome.error_code`): `invalid_request` (400), `unauthorized` (401), `forbidden` (403),
`not_found` (404), `rate_limited` (429), `unavailable` (503 oder nicht erreichbar), `http_<status>`,
`deadline_exceeded`, `bad_response`, `model_not_allowed`, `schema_not_allowed`, `invalid_schema`,
`too_many_images`, `image_too_large`, `invalid_image`, `image_decoder_unavailable`, `provider_error`.

- Ein Decision-Wert erteilt nie selbst eine Berechtigung (`grants_permission` ist immer `False`); `act` entsteht
  nur aus der Hub-Policy.
- Mitgefuehrt pro Feld: `probability`, `margin`, `entropy` (nats), `abstain`, `abstain_reasons`, optional `probs`;
  pro Request `usage` (`prompt_tokens`, `cached_tokens`, `media_chunks`, `media_tokens`, `media_cached`) und
  `timings` (`prefill_ms`, `media_encode_ms`, `scoring_ms`, `total_ms`, `rounds`).
- Logs: nur Schema-ID, ok/Fehlercode, HTTP-Status, Kontextanzahl, Anzahl abstained, `media_tokens`,
  `media_cached`, `total_ms`, Latenz. Keine Bilder, kein Base64, keine Prompts, keine Werte, kein Schluessel.
  `as_audit_dict()` enthaelt keine Werte.

## Grenzen (unbedingt lesen)

**Schema-gueltig heisst nicht richtig.** Der Runner waehlt immer einen erlaubten Wert, auch wenn die Frage zum
Bild nicht passt.

Modellmatrix des Forks (CPU, 256x256-Shape-Testset aus `bench/shapes.py`, 4 Felder; `bench/evaluate.py`):

| Modell | schwaechstes Feld | Genauigkeit | mittlere Konfidenz | Einsatz |
|---|---|---|---|---|
| Qwen3-VL-2B-Instruct Q8_0 | count / dark_background | 0.94 | 0.99 | einzig empfohlenes Modell (>= 94 % auf allen Feldern) |
| Qwen2.5-VL-3B-Instruct Q4_K_M | dark_background | 0.61 | 0.80 | nicht empfohlen: uebersicher, Q4 auf CPU split-instabil |
| SmolVLM-500M-Instruct Q8_0 | dark_background | 0.47 | 0.89 | nicht verwenden: Zufallsniveau bei hoher Konfidenz |

- Die Zahlen gelten nur fuer dieses synthetische Testset; fuer echte Aufgaben vorher mit gelabelten Bildern messen
  (`bench/evaluate.py`, Temperatur fitten) und die Schwellen pro Aufgabe setzen.
- Eigene Beobachtung mit Qwen3-VL-2B (2026-09-23): auf einer leeren grauen Flaeche abstainen `shape`, `color`,
  `count` korrekt (p 0.33-0.59), aber `dark_background=true` kommt mit p=1.0 bei mittlerem Grau; auf einem Bild mit
  rotem Kreis **und** blauem Quadrat liefert es `shape=circle`, `color=red` mit p=1.0. Abstain faengt schlecht
  gestellte Fragen nicht zuverlaessig ab: Felder sicherheits- oder rechtsrelevanter Entscheidungen immer ueber
  `human_review_fields` oder Policy `confirm` fuehren.
- Keine OCR, kein Freitext: Rechnungsnummern, IBAN, Namen usw. gehoeren in eine Chat-Completion.
- Nur Bilder: Audio und Video lehnt der Fork ab; Ananta sendet nur PNG.
- Performance: alle Zahlen sind CPU-Zahlen (Ryzen 9 7940HS, 6 Threads, ohne CUDA; die RTX 5060 Ti ist mangels
  CUDA-Toolkit nicht gemessen). Qwen3-VL-2B: ein Bild mit 4 Feldern ca. 1.2-2.5 s, erste Anfrage nach dem Start
  laenger; `TIMEOUT_MS` entsprechend hoch lassen.
- Parallele Felder sehen einander nicht; abhaengige Felder sequenziell oder per Chat/System-2 abfragen.

## Tests

```
docker exec -w /app compose-next-ai-agent-hub-1 env -u AGENT_TOKEN_FILE python -m pytest -q -p no:cacheprovider \
  tests/test_vision_decision_provider.py
docker exec -w /app compose-next-ai-agent-hub-1 env -u AGENT_TOKEN_FILE RUN_INTEGRATION_TESTS=1 \
  VISION_DECISION_IT_URL=http://172.17.0.1:8096 VISION_DECISION_IT_MODEL=Qwen3-VL-2B-Instruct-Q8_0.gguf \
  python -m pytest -q -s -rs -p no:cacheprovider tests/test_vision_decision_integration.py
```

Ohne `RUN_INTEGRATION_TESTS=1`, ohne `VISION_DECISION_IT_URL` oder ohne erreichbaren Server ist der
Integrationstest SKIPPED, nie bestanden.

Ergebnis 2026-09-23 (Qwen3-VL-2B Q8_0, CPU, Server auf `172.17.0.1:8096`, aus dem Hub-Container):
5 passed. 8 Shape-Bilder einzeln: 32/32 akzeptierte Felder korrekt, 0 abstained, `media_tokens=64` pro Bild,
`total_ms` 1.2-2.5 s. Zwei Bildkontexte in einem Request: kalt 2.41 s (`media_encode_ms` 906, erster Lauf nach Serverstart), warm
1.43 s mit `media_cached=2`, gleiche Entscheidungen (der Bild-Cache ueberlebt Requests; ein zweiter Testlauf
sieht daher schon beim ersten Request `media_cached=2`). Leeres graues Bild: 3 Felder abstained und eskaliert. Server-400 wird zu
`invalid_request`/`no_decision`. Zusaetzlich 32x32-Bilder `11_truck.png` -> `vehicle`, `91_cat.png` -> `animal`
(je p=1.0; nur protokolliert, nicht bewertet).
