# Ananta Meet Companion Avatar (ai-snake)

Der persistente Meet-Companion (`worker/meet_media/companion.py`) erscheint als
freundliche Ananta-Schlange, spricht Antworten mit synchroner Mundbewegung und
kann Ananta sowie seine eigene Pipeline anhand des Repositories erklären.

Track: `todos/todo.ananta-meet-codecompass-avatar.json`.

## Feature Flags

| Flag | Default | Wirkung |
|---|---|---|
| `ANANTA_MEET_AVATAR_ENABLED` | `1` | Avatar-Clips (idle/thinking/speaking) publizieren; bei `0` nur Sprache + Chat. |
| `ANANTA_MEET_AVATAR_CODECOMPASS` | `1` | Ananta-/Repository-Fragen über den Hub-CodeCompass-Kontext grounden. |
| `MEET_COMPANION_TOOLS` | `1` | CodeCompass dem Chat-Modell als aufrufbares Werkzeug (`codecompass_search`) anbieten – das Modell entscheidet selbst. |
| `MEET_COMPANION_RAG_PREFIX` | `0` | Bisheriges Verhalten: abgerufene Snippets jeder gerouteten Frage voranstellen. Lässt sich mit dem Tool kombinieren. |

`0`, `false`, `off`, `no` deaktivieren; alles andere aktiviert. `ANANTA_MEET_AVATAR_CODECOMPASS=0`
schaltet beide Wege ab; sind Tool und Prefix aus, antwortet das Modell ungegroundet.

| Variable | Default | Wirkung |
|---|---|---|
| `MEET_LLM_TOOL_ROUNDS` | `2` | Werkzeug-Runden pro Antwort (0..4). Die letzte Anfrage wird ohne `tools` gestellt. |
| `MEET_LLM_TOOL_RESULT_CHARS` | `1400` | Obergrenze eines Werkzeug-Ergebnisses im Prompt. |

| Variable | Default | Wirkung |
|---|---|---|
| `MEET_AVATAR_SERVICE_ENABLED` | `1` | Sprech-Clips über den lokalen MuseTalk-Lippensync-Dienst rendern (`0` = nur lokaler Renderer). |
| `MEET_AVATAR_SERVICE_URL` | `http://172.18.112.1:8189` | Basis-URL des Dienstes (`GET /health`, `POST /avatar`). |
| `MEET_AVATAR_SERVICE_PORTRAIT` | `/state/ananta-snake-portrait.png` | Referenzportrait (Asset, 512×512 PNG), das der Dienst als `image_png_b64` erhält. Fehlt es, bleibt Lippensync aus (lokaler Renderer). |
| `MEET_AVATAR_IDLE_CLIP` | `/state/ananta-snake-idle.mp4` | Vorgerenderter, nahtlos loopender Idle-Clip (3 s, 36 Frames, 256×256, 12 fps), publiziert als `persona-video-v1` mit `repeatMode="loop"`. Fehlt er, rendert der lokale `IdleClips`-Renderer. |
| `MEET_COMPANION_AVATAR_REFRESH` | `25` | Sekunden, nach denen der Idle-Clip neu geöffnet wird; die Client-Aktivierung ist auf 120 s und die ursprüngliche Hub-Deadline begrenzt. |

## Lippensync über den MuseTalk-Dienst

`worker/meet_media/avatar_service.py` kapselt den Dienst:

* `render(portrait_png, wav, base_url=…)` – ein begrenzter HTTP-Roundtrip
  (`POST /avatar` mit `image_png_b64`/`audio_wav_b64`, Timeout 20 s, ein Retry
  bei 429 nach gedeckeltem `Retry-After`). Jeder Fehler wird zu
  `AvatarServiceError` mit `reason_code`
  (`meet_avatar_service_{busy,rejected,failed,timeout,unreachable,response_invalid,clip_invalid,clip_too_large,frames_invalid}`).
  Die Antwort wird validiert (ftyp-Container, 1..120 Frames, ≤ 2 MB base64).
* `LipSyncClient` – hält das Portrait, liefert `persona-video-v1`-Payloads
  (`video_payload`: mp4, sha256, frames, `repeatMode="hold_last"`,
  `originKind="generated"`, `classification="synthetic"`) und gibt bei
  Fehlern `None` zurück. Nach einem Fehler bleibt er 30 s still, damit ein
  unerreichbarer Dienst höchstens einen Timeout pro Antwort kostet.
* `SpeechAvatarPublisher(lipsync=…)` ruft den Port **pro `ClipSegment`** mit
  genau dessen PCM-Fenster (≤ 10 s, die Dienstgrenze) auf. `None` wählt für
  dieses Segment den lokalen Schlangen-Renderer; die Timeline, die
  Swap-Zeitpunkte und die Drift-Messung bleiben unverändert.
* Das Portrait ist ein **Asset** (`avatar_service.load_portrait(portrait_path())`,
  `MEET_AVATAR_SERVICE_PORTRAIT`), nicht mehr ein vom Worker gerendertes Bild:
  der Dienst bestimmt die Gesichtsregion aus genau diesem Bild (`face_method`
  im `/health`-Feld `last_inference`; `avatar_service.health_report`). Das
  aktuelle Portrait (`data/meet-media/worker-state/ananta-snake-portrait.png`,
  siehe `snake-README.md` dort) wird vom Detektor nicht als Gesicht erkannt
  (`centered_fallback`, Stand 2026-09-22); ein Face-Box-Override
  (`AVATAR_FACE_BOX`) ist Dienst-seitig und wird vom Worker nicht gesetzt.
  Fehlt das Asset, wird der Lippensync deaktiviert und der lokale Renderer
  übernimmt die Sprech-Clips.
* Im Leerlauf (`idle`, `thinking`, `listening`) publiziert der Companion den
  vorgerenderten Idle-Loop (`avatar_idle_clip.IdleClipSource`,
  `MEET_AVATAR_IDLE_CLIP`) als `persona-video-v1` mit `repeatMode="loop"`; die
  Frame-Zahl wird aus dem MP4 (`stsz`) gelesen. Der prozedurale
  `IdleClips`-Renderer ist nur noch Fallback, wenn das Asset fehlt oder
  ungültig ist (`idle clip fallback reason=…` im Log).

Gemessen (RTX 5060 Ti): 2 s Audio → 24 Frames in ≈ 4,6 s, 10 s → 120 Frames
in ≈ 9,8 s. Die Clips werden vor dem Sprechen gerendert, d. h. eine 40-s-Antwort
wartet bis zu ≈ 40 s im Zustand `thinking`. Bekannte Grenze: MuseTalks
Detektor erkennt weder das prozedurale noch das neue Cartoon-Portrait als
Gesicht (`centered_fallback`); der generierte Mundbereich bleibt sichtbar
weicher als die Illustration. Eine manuelle Gesichtsbox ist Dienst-seitig
möglich (siehe `snake-README.md`).

## Synchronisierte Audio/Video-Pipeline

```text
Reply-Text -> Piper TTS (einmal) -> PCM s16le 22.05 kHz
                                     |
             +-----------------------+------------------------+
             |                                                |
   speech.push (20 ms Frames,                 SpeechMediaTimeline(samples)
   120 ms Vorlauf vor Playback)               -> Frame-Fenster = Sample-Index
             |                                -> Envelope + Silence-Gate
             |                                -> ClipSegment(<=120 Frames)
             |                                -> AnimationController -> FramePose
             |                                -> render_pose -> H.264 Clip
             |                                                |
   Playback-Anker t0 = erster Push        avatar.open(clip_i) exakt bei
                                          t0 + segment_i.start_us
```

* **Eine Zeitbasis:** Jeder Videoframe ist die Projektion eines Sample-Index
  desselben PCM-Puffers, der als Sprachtrack publiziert wird
  (`snake_avatar_timeline.SpeechMediaTimeline`). Es gibt keinen zweiten,
  unabhängig gestarteten Ablauf.
* **Lange Antworten:** `persona-video-v1` erlaubt maximal 120 Frames pro Clip.
  Die Timeline zerlegt die Antwort in `ClipSegment`s; `SpeechAvatarPublisher`
  wechselt den Clip auf dem Playback-Clock (nicht dem Push-Clock) genau an der
  Segmentgrenze.
* **Drift-Messung:** `SpeechMediaTimeline.measure_drift` vergleicht die
  beobachtete Clip-Öffnung mit `t0 + start_us` des Segments und wirft
  `meet_avatar_drift_exceeded`, wenn ein Clip mehr als einen halben Frame
  (41 ms) abweicht. Der Companion loggt `SYNC max_drift_us=…` bzw. `SYNC_ERR`.
  Test: `tests/meet/test_meet_snake_avatar_sync.py` (3,3 s und 31 s Antworten).
  Die browserseitige Wahrheit bleibt das bestehende `MediaTimingGate`.
* **Lip-Sync (MVP):** RMS-Envelope pro Frame, Silence-Gate schließt den Mund in
  Pausen, Attack/Release < 1 Frame. Phonem-/Visem-Mapping kann später als
  zusätzliche Quelle für `FramePose.mouth` auf derselben Timeline ergänzt werden.

## Avatar-Zustände

`snake_avatar_state.AvatarStateMachine` kennt `idle`, `listening`, `thinking`,
`speaking` mit weichen 250-ms-Übergängen. TTS-Start/-Ende setzen `speaking`
deterministisch. Blinzeln (`BlinkSchedule`) ist vom Mund entkoppelt; Kopf-Nicken
folgt einer tiefpassgefilterten Envelope, damit Gesten ruhig bleiben.
`thinking` zeigt zusätzlich eine Denkblase, damit der Zustand auch im kleinen
Teilnehmerbild erkennbar ist.

## Dialog, Routing und Self-Explanation

`companion_dialog.CompanionDialog` orchestriert pro Nachricht:

1. `companion_router.classify` (deterministisch, Keyword-basiert):
   `self_explanation` → Antwort aus dem Ablaufprotokoll ohne Modellaufruf;
   `ananta_code_architecture` → CodeCompass-Kontext über den Hub
   (`/api/meet/v1/internal/assist/retrieve`, Worker-Key signiert);
   `meet_runtime_current_dialog` → Runtime-Kontext plus CodeCompass bei
   Code-Bezug; `general_question` → nur das konfigurierte lokale Modell.
2. Grounding: Snippets liefern `path`, `symbol`, `revision` (Commit-SHA, sofern
   der Index sie führt) und werden als `[pfad#symbol] excerpt` in den Kontext
   gestellt.
3. Generierung über das lokale Ollama-/OpenAI-kompatible Modell
   (`llm.answer`, Persona-Systemprompt `PERSONA_SYSTEM`, mit Werkzeug
   `PERSONA_SYSTEM_WITH_TOOLS`).
4. `companion_explanation.AnswerTrace` protokolliert getrennt **beobachtete
   Runtime-Schritte**, **Repository-Quellen** und **den generierten Text**.
   "Wie hast du diese Antwort erzeugt?" wird daraus deterministisch beantwortet;
   fehlt Evidenz, sagt die Schlange das, statt Dateien zu nennen.

## CodeCompass als Werkzeug (Function Calling)

Seit `MEET_COMPANION_TOOLS=1` (Default) bekommt das Chat-Modell den Wissensindex
nicht vorgekaut, sondern als aufrufbare Funktionen; es entscheidet selbst, ob
und wie oft es nachschlägt. Backend ist Bonsai 2 27B über llama-server
(`MEET_LLM_BACKEND=openai`), das native `tool_calls` liefert.

### Ablauf einer Chat-Frage

```text
Chat-Nachricht → companion.py (Playwright, chat.poll)
  → companion_router.classify: Wissensfrage? → erzwungene codecompass_search
    mit RouteDecision.query (Identifier zuerst, sonst die Frage ohne Frage-/Füllwörter)
  → Modell mit 8 Tools, ≤ MEET_LLM_TOOL_ROUNDS Runden, ≤ MEET_LLM_TOOL_CALLS Aufrufe
  → jeder Aufruf: Worker → Hub (HMAC mit dem Worker-Key)
       codecompass_search  → /internal/assist/tool (codecompass.retrieve)
                             Fallback /internal/assist/retrieve bei älterem Hub
       die 7 anderen Tools → /internal/assist/tool → Hub-MCP-Registry
  → Hub: KnowledgeIndexRetrievalService.search_records_page
  → Ergebnis als role:"tool" → Antwort → Chat + Sprache
```

Der Worker spricht nie MCP und hält weder Index noch Credential. Der Hub prüft
jede Anfrage: feste Allowlist von 8 Nur-Lese-Tools
(`ANANTA_MEET_COMPANION_MCP_TOOLS` kann einengen, nie erweitern), Companion-
Identität nur für Projekte im Media-Scope, Operation-Policy, Audit je Aufruf.

### Die Werkzeuge (`llm_tools`)

| Modell-Name | MCP-Tool | Zweck |
|---|---|---|
| `codecompass_search` | `codecompass.retrieve` | Auszüge aus dem Wissensindex |
| `codecompass_architecture_overview` / `_expand` | `codecompass.architecture_*` | gestufter Architekturüberblick, Handles (`hac:…`) vertiefen |
| `codecompass_architecture_intelligence` | `codecompass.architecture_intelligence` | Communities, Smells, Gesundheit |
| `codecompass_layers_heads` / `_plan` | `codecompass.layers_*` | Index-Layer lesen, Update-Trockenlauf |
| `codecompass_analytics_query` | `codecompass.analytics_query` | benannte Vorlagen, kein freies SQL |
| `codecompass_rlm_analyze` | `codecompass.rlm_analyze` | rekursive Analyse, Tiefe ≤ 2, Fanout ≤ 3 |

`MEET_LLM_TOOLSET=search` bietet nur `codecompass_search` an.

### Was das Modell von einer Suche sieht

```text
codecompass_search 'companion supervisor': 5 von 37 Treffern, die besten folgen (Datei:Zeilen, Score).
[worker/meet_media/companion_supervisor.py:30-44#supervise score=9.4]
def supervise(run, stop_requested, *, clock, sleep, log, backoff=RejoinBackoff()):
    …
```

* **Trefferzahl:** `total` zählt alle passenden Datensätze im Projekt-Scope,
  nicht nur die gelieferten (`search_records_page`).
* **Passage statt Dateianfang:** Der Hub wählt je Treffer das Fenster der
  Datei, das die Suchbegriffe enthält (`knowledge_passage.best_passage`,
  ≤ 1200 Zeichen), mit Zeilenbereich `line`–`line_end`. Den `# pfad`-Kopf,
  den `setup_codecompass_index.py` voranstellt, rechnet er heraus; ein Index
  mit eigenen `start_line`-Angaben verschiebt den Bereich entsprechend.
  Zeilenumbrüche bleiben erhalten, Code bleibt lesbar.
* **Budget:** ein Tool-Ergebnis ≤ `MEET_LLM_TOOL_RESULT_CHARS` (Default 6000,
  wie die Hub-Kürzung), sodass alle 5 Standard-Auszüge ankommen. Query ≤ 200
  Zeichen, `limit` 1..8.
* **Grenzen, die bleiben:** Die Suche ist lexikalisch (Token-Treffer, keine
  Embeddings). Zeilen sind so aktuell wie der Index: ein veralteter Index
  liefert die Zeilen seines Stands.

### Projekt-Scope

`ANANTA_MEET_COMPANION_KNOWLEDGE_SOURCES` (Hub) bindet Projekte an
Indexquellen, z. B.
`{"ca1388ef-…": ["ananta-project"], "*": ["ananta-project"]}`. Ein Selektor
trifft Index-ID, `source_id` oder `connection_source_id`. Gebundene Projekte
(und `"*"`) sind strikt, auch für Legacy-Indizes; eine leere Menge durchsucht
nichts. Ohne Bindung bleibt die bisherige, ungefilterte Suche über alle
fertigen Indizes, inklusive synthetischer Test-Indizes. Deshalb im Betrieb
setzen.

### Nachvollziehbarkeit

Jeder Aufruf landet in der `AnswerTrace` und im Companion-Log
(`TOOL codecompass_search query=… snippets=5 total=37 …`), damit
"Wie hast du diese Antwort erzeugt?" die Wahrheit sagt. Das Ergebnis bleibt
untrusted Referenzmaterial: Systemprompt, Markdown-Strip und
`max_reply_chars` gelten unverändert.

Ollama (`/api/chat`) unterstützt dieselbe Schleife; dort gibt es keine
`tool_call_id`, die Zuordnung läuft über den Namen.

Der Worker importiert kein Hub-`agent`-Paket (`scripts/check_meet_worker_boundaries.py`).

## Wiederbeitritt nach Sitzungsverlust

Eine Companion-Sitzung endet, wenn der Room-Server eine Lease-Verlängerung
ablehnt (`machine_renewal_denied`, z. B. 409/502), die Seite die Mitgliedschaft
verliert oder der Hub kurz nicht erreichbar ist. `companion_supervisor.supervise`
startet danach eine neue Sitzung mit frischem Hub-Grant: nach einem Raumwechsel
sofort, sonst mit exponentiellem Backoff (5 s bis 300 s, Reset nach einer
Sitzung von mindestens 10 Minuten). Jeder Wiederbeitritt bleibt damit vom Hub
autorisiert. Beendet wird der Companion nur über die Stop-Datei
(`MEET_COMPANION_STOP`, Default `/state/companion-stop`), die auch eine laufende
Wartezeit abbricht.

Start im Meet-Media-Worker-Container:

```bash
docker exec -d ananta-meet-media-meet-media-worker-1 python -m worker.meet_media.companion
```

## Tests

```bash
cd docker/compose-next
docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm t-infra \
  sh -c "python -m pytest -q tests/meet/test_meet_snake_avatar_sync.py tests/meet/test_meet_companion_dialog.py tests/meet/test_meet_llm_tools.py tests/meet/test_meet_assist_retrieve_route.py tests/meet/test_meet_avatar_service.py tests/meet/test_meet_companion_supervisor.py tests/meet/test_meet_companion_public_room.py tests/meet/test_meet_companion_knowledge_scope.py tests/meet/test_meet_llm_tools_hub.py"
```

`tests/meet/test_meet_avatar_service.py` mockt den Dienst (Wire-Format, Fehlercodes,
Fallback-Policy); der markierte Integrationstest ruft den echten Dienst nur mit
`RUN_INTEGRATION_TESTS=1` und erreichbarem `/health` auf, sonst wird er übersprungen.
`tests/meet/test_meet_avatar_assets.py` deckt Portrait-Auswahl (Env, fehlend,
ungültig) und den Idle-Clip-Pfad (MP4-Frame-Zählung, Loop-Payload, Cache,
Fallback) mit synthetischen Dateien ab; der Integrationsteil lädt die echten
Assets aus `data/meet-media/worker-state/` und ruft den Dienst mit dem Portrait
auf, sofern erreichbar.

Die Tests laufen ohne FFmpeg (injizierter Encoder) und ohne Browser (Ports als
Fakes mit deterministischer Uhr).

Die Chat-Diagnostik projiziert nur validierte Kurz-IDs und Zustandszähler;
RECV/REPLY/STATE enthalten keine Nachrichtentexte, Peer-Payloads oder Chat-Dumps.
Chat-Fehler werden durch feste Codes ersetzt. Ein erneuter Versand einer
gehaltenen Antwort ist ausschließlich für die bekannten Port-Fehler erlaubt,
einschließlich des Playwright-Error-Präfixes; ein zitierter Fehlercode in einem
anderen Fehler reicht nicht. Nachrichten-IDs müssen vollständig dem Format
entsprechen. Die Projektion ist ein eigener kleiner Baustein (SRP/ISP);
der breite Companion-Loop bleibt bestehende SRP-Schuld. Diese lokale Prüfung
ersetzt keinen Browser-/Deployment-Live-Gate.

Der produktive Companion-Loop nimmt jetzt während Modell-/TTS-Ausführung
weiter Chat-Batches an und erneuert über den Hub die Sitzung. Browserzugriffe
bleiben im Besitzer-Thread; ein lokaler Hilfsprozess hält die Dialog-Ausführung
und wird bei Timeout oder Kontrollverlust einschließlich Unterprozessen beendet.
Die Inbox übernimmt nur begrenzte, bereits zugestellte Eingaben. Ein vertrauenswürdiger
Browser-open-Receipt bindet Origin, Tenant, Project, Task, Session, Runtime,
Lease, Room und eigenen Peer; andere Bindungen oder rückläufige Revisionen
verwerfen gehaltene Daten. Reopen setzt Queue-Cursor zurück; ACKs bestätigen
nur verarbeitete aktuelle Cursor und überspringen keine gehaltenen Eingaben.
Clients ohne funktionierende Cursor-Pagination beenden diesen Pfad begrenzt.

354 lokale Tests prüfen unter anderem den echten `run()`-Loop mit einer
synthetischen 50-s-Modellrunde, zweiter Eingabe währenddessen, Renewal und
120-s-Antwortfenster. Das ist kein Live-Browser-/GPU-/Deployment-Nachweis.
Dieser datierte Zwischenstand wurde für größere Rückstaus inzwischen ergänzt.
Auch die damals blockierenden Renderer/Lipsync-Pfade sind inzwischen durch den
unten beschriebenen Media-Port umgestellt. Die gemeinsame Live-Abnahme von
Client und Worker bleibt offen. Der ursprüngliche Prüfbericht steht
in `artifacts/test-gates/meet-companion-cooperative-composition.v1.json`.

Die additive Browser-Operation `chat.redeliver(messageIds)` bestätigt nur eigene,
bereits zugestellte, noch gültige und unbeantwortete IDs unter aktueller
Lese-/Quellen-/Lease-Autorität erneut. Maximal 32 eindeutige IDs werden vor jeder
Zeitstempeländerung vollständig geprüft; unbekannte, beantwortete oder bereits
abgelaufene IDs können nicht zurückgeholt werden. Sie liefert keinen neuen
Inhalt, sendet keinen ACK und erweitert weder Senderechte noch Lease-Deadline.
Die 30-s-Erstaufnahme und das 120-s-Antwortfenster ab letzter gültiger Zustellung
bleiben erhalten. Der Pump bestätigt offene, noch nicht beantwortete IDs
höchstens alle zehn Sekunden. Fehlt die Operation oder passt ihr geschlossenes
Receipt nicht, endet die aktuelle Sitzung begrenzt und verwirft die Inbox.

364 Companion-Regressionstests bestehen, einschließlich des echten Python-
`run()` mit dem tatsächlichen gebündelten TypeScript-Endpoint und Queue in Node:
2, 3 und 32 Eingaben werden bei je 50 Sekunden synthetischer Modelllaufzeit
genau einmal beantwortet, während der Hub-Port die Lease regelmäßig erneuert.
Die frühere Drei-Nachrichten-Reproduktion verlor die dritte Antwort.
Zusätzlich bestehen 47 Endpoint- und 28 Queue-Tests sowie der App-Typcheck.
Browsertransport, Hub-Grants und Modelle sind in dieser Abnahme synthetisch;
das ist kein echter Browser-, Provider-, GPU- oder Registry-Release-Nachweis.
Der Bericht steht in `artifacts/test-gates/meet-chat-delivery-revalidation.v1.json`.

Für den optionalen echten Node-Endpunkt-Test wird der aktuelle Client aus dem
angrenzenden Repository lokal gebündelt; ohne beide expliziten Pfade wird nur
dieser zusätzliche Transport sichtbar übersprungen:

```bash
cd /home/krusty/webrtc-minimize-server
node_modules/esbuild/bin/esbuild frontend/src/app/features/machine/machine-chat-endpoint.ts \
  --bundle --platform=node --format=esm --outfile=/tmp/meet-chat-endpoint.mjs
cd /home/krusty/ananta
ANANTA_TEST_NODE_BINARY=/path/to/node \
ANANTA_TEST_MEET_CHAT_ENDPOINT_BUNDLE=/tmp/meet-chat-endpoint.mjs \
python -m pytest -q tests/meet/test_meet_companion_polling_composition.py
```

Client und Worker müssen gemeinsam abgenommen und deployt werden; ein älterer
Client ohne diese Operation kann diesen Pfad mit offenen Eingaben nicht bedienen.
Die getrennten Ports schützen SRP/ISP/DIP. Das breite Companion-Modul und der
Import seiner Modell-/TTS-Komposition durch den Hilfsprozess bleiben SRP-/DIP-
Schulden; eine spätere Extraktion in injizierte Runtime-Factories bleibt offen.

Die zuvor offene blockierende Vorbereitung im Legacy-`run()` ist jetzt ebenfalls
lokal umgestellt: Idle-Asset/Fallback, Lipsync-HTTP und FFmpeg laufen in einem
eigenen, vom Browser-Besitzer kontrollierten Media-Hilfsprozess. Während des
Wartens bedient der Besitzer weiterhin die Chat-/Hub-Control-Ports. Widerruf,
Timeout und ungültige Receipts beenden den besessenen Prozess einschließlich
seiner Prozessgruppe; es gibt keine Worker-Delegation oder zusätzliche Taskqueue.
Ein gemeinsamer JSON-Pipe-Transport kapselt Lifecycle und Kooperation, getrennte
Modell-/TTS- und Medienadapter prüfen ihre jeweiligen geschlossenen Protokolle.

Medienanfragen sind auf 2,6 MB, Antworten auf 10,1 MB, PCM auf 1.940.400 Bytes
und Ergebnisse auf fünf synthetische Clips mit je maximal zwei Millionen
Base64-Zeichen beschränkt. Formatlabels, Clip-Hash und Shape-Prüfung erteilen
keine Publishing-Rechte und sind keine Registry-Evidence. Der bestehende
Browser führt seine Admission weiterhin selbst aus. Die Vorbereitung ist auf
120 Sekunden begrenzt; der Playback-Anchor entsteht erst nach vollständiger
Vorbereitung. Dauerhaft volle Speech-Queues enden nach Sprechdauer plus
30 Sekunden Stallbudget mit `meet_avatar_publication_timeout`.

418 Tests im zusammenhängenden Lauf und 18 abschließende Medien-Tests mit
17 Überschneidungen bestehen, insgesamt 419 unterschiedliche Tests. Zwölf
`run()`-Fälle prüfen 2/3/32 Eingaben, je 50 Sekunden Modellrunde, und zusätzlich
50 Sekunden Idle-/Sprechvorbereitung bei aktiviertem Avatar; sechs davon laufen
gegen den tatsächlichen TypeScript-Endpoint in Node. Der echte Hilfsprozess
führt den lokalen Renderer und CPU-FFmpeg aus; ffprobe bestätigt H.264,
256×256, 12 fps, reine Videotracks und tatsächliche Framezahlen. Langsame
Lipsync-HTTP-Antworten, HTTP-500-Fallback zum Renderer und Widerruf während
einer laufenden HTTP-Anfrage sind über einen synthetischen Loopbackdienst
geprüft. Pillow 11.3.0 und NumPy 2.2.6 entsprechen dabei den Worker-Pins.

Für die zusätzlichen echten CPU-/HTTP-Fälle wird der Gate explizit aktiviert:

```bash
ANANTA_TEST_REAL_COMPANION_MEDIA=1 \
python -m pytest -q tests/meet/test_meet_companion_media_process.py
```

Ohne Aktivierung werden nur diese zusätzlichen Integrationsfälle sichtbar
übersprungen; ein ausdrücklich aktivierter Gate ohne FFmpeg/ffprobe schlägt
begrenzt fehl. Ein bestehender echter Avatar-Service-/GPU-Live-Test bleibt
übersprungen. CPU-Fallback und synthetischer HTTP-Dienst beweisen weder
GPU-/NVENC-/Portrait-Abnahme noch echte Browser-/Deployment-/Registry-Freigabe.
Der aktuelle Nachweis ist
`artifacts/test-gates/meet-companion-media-preparation.v1.json`.

Die Trennung verbessert SRP und ISP/DIP: Browser-Publishing, Medienprotokoll und
Prozess-Lifecycle liegen in eigenen Komponenten. Die globale Konfiguration und
breite Factory-Komposition des Legacy-Companions bleiben erhaltene SRP-/DIP-
Schulden. Kompatible direkte Vorbereitungsmethoden bleiben synchron; der
produktive `run()` injiziert den abbrechbaren Media-Port.

Der eigenständige Meet-Worker wurde inzwischen mit dem unveränderten produktiven
`docker/meet-media/Dockerfile` frisch gebaut. Der neue optionale Test
`tests/meet/test_meet_companion_packaged.py` prüft zehn aktuelle Module im Image
gegen ihre Quellcode-Digests und führt die echte Dependency-Lock-Prüfung aus.
Der Container läuft mit UID 11111, ohne Capabilities, mit NoNewPrivs,
schreibgeschütztem Root-Dateisystem, ausschließlich flüchtigem `/tmp` und
`/state`, ohne Netzwerk und ohne GPU oder Repository-Bind-Mount. Die effektiven
Grenzen werden zusätzlich im Container geprüft. Das Hub-Paket fehlt.

Der originale Modell-Helper-Entry lehnt einen unbekannten Befehl vor jeder
Modellausführung ab. Ein explizites synthetisches Modell-Double prüft den
persistent gehaltenen Pipe-Prozess; der originale Media-Entry rendert Idle und
Sprechclips mit CPU-FFmpeg. Ein Widerruf beendet den besessenen Media-Prozess.
Das ist ein Packaging-/Ausführungsnachweis ohne Live-LLM-/TTS-/GPU-Abnahme.
Der bestehende paketierte Health-Gate prüft außerdem gesund → ungesund bei
gestopptem Listener → gesund nach Fortsetzung, ohne Containerneustart oder Task.

Beide Gates benötigen die explizite unveränderliche ID des lokal gebauten Images:

```bash
MEET_COMPANION_PACKAGED_GATE=1 MEET_COMPANION_PACKAGED_IMAGE='sha256:<image-id>' \
python -m pytest -q tests/meet/test_meet_companion_packaged.py
MEET_WORKER_HEALTH_GATE=1 MEET_WORKER_HEALTH_PACKAGED=1 \
MEET_WORKER_HEALTH_IMAGE='sha256:<image-id>' \
python -m pytest -q tests/meet/test_meet_worker_health_container.py
```

Ohne Gate werden diese Containerfälle sichtbar übersprungen. Tests besitzen
und bereinigen nur ihre eigenen Container; keine laufende Instanz wird verändert.
Der Build braucht bei fehlenden Cache-Layern Paket-Netzwerkzugriff, die Prüfcontainer
bleiben netzwerkisoliert. Ein Deployment oder eine Registry-Release-Freigabe folgt
daraus nicht. Der aktuelle Bericht ist
`artifacts/test-gates/meet-companion-packaged.v1.json`.


Der aktuelle Angular-Client und ein eigener HTTPS-/Signaling-/WebRTC-Server
bestehen vier echte Browserfaelle: Chromium/Chromium und Firefox/Chromium fuer
Chat, PCM und Bildschirm; Chromium/Chromium und Firefox/Firefox fuer Nur-Lese-Chat.
Neun offene Eingaben werden ohne ACK als 8+1 paginiert, durch drei echte
Hub-Lease-Renewals erneut bestaetigt und je einmal beantwortet. Nur-Lese-Receipts
erteilen kein Senderecht. SFrame ist aktiv, synthetisches PCM wird tatsaechlich
transportiert und bewegte Bildschirmframes werden dekodiert.

Ein separater Nachlauf derselben zwei Nur-Lese-Faelle prueft den menschlichen
Publisher-Verlust bei weiterhin verbundener Maschine und gueltiger Hub-Lease:
Die alte Eingabe kann nicht erneut bestaetigt werden. Anschliessender Verlust
der Maschinen-Mitgliedschaft sperrt Wiedereroeffnung und Redelivery ebenfalls.
Die Faelle werden nicht als weitere unterschiedliche Matrixfaelle gezaehlt.

Die Receive-Policy folgt dem bestehenden Client-Commit `add3f5f`: verifizierte
Hub-Capabilities, aktuelle Lease und Membership autorisieren Empfang. Alte
Publisher-Consent-Eintraege erteilen oder widerrufen keine Hub-Capability.
Veraltete Testerwartungen wurden an diese bestehende Policy angepasst; keine
Produktivpolicy wurde geaendert. Die Quellenfreigabe-UI nennt diese alten
Eintraege noch irrefuehrend aktuelle Empfangsfreigaben und muss separat
angepasst werden (erhaltene SRP-/Sicherheitsbeobachtbarkeits-Schuld).

Dies prueft echte Browser mit ausschliesslich synthetischen Testidentitaeten
und eigenen lokalen Ressourcen. Die Browsermatrix nutzt Playwright 1.62.1;
der Python-Worker ist auf 1.58.0 gepinnt. Ein aktueller Python-Worker-/Browser-
Gesamtloop unter langer Modell-/Medienarbeit und Quellenwechseln ist damit
noch nicht bewiesen. GPU-/Live-LLM-/TTS-, Block- und Deployment-Abnahme sowie
Hub-Registry-Release-Evidence bleiben offen. Der Nachweis steht in
`artifacts/test-gates/meet-chat-browser-redelivery.v1.json`.


Die lokale Receive-Policy-Migration ist inzwischen weitergefuehrt: Die menschliche
Quellenuebersicht ist passiv und zeigt Hub-Rechte fuer Audio, Bild, Chatlesen und
Antworten getrennt. Unwirksame Legacy-Consent-Schalter sind aus der aktiven UI
entfernt. Stoppen erfolgt ueber die bestehenden Mediensteuerungen oder Leave.
Auch PCM- und Bildexport benutzen jetzt die schon geltende Hub-Capability-Policy
statt einer zusaetzlichen alten Consent-Anforderung; aktuelle Lease, Membership,
Quelle/Track, Publikationsepoche und begrenzte Export-Lifetimes bleiben Pflicht.
139 Frontend- und 14 Node-Tests bestehen. 22 unterschiedliche echte Browserfaelle
bestehen ueber Matrix und gezielten Renewal-Nachlauf; der anfaengliche Lauf hatte
20 Erfolge und zwei falsche Renewal-Testannahmen und wird nicht als komplett
gruener Gesamtlauf dargestellt. Faehigkeitswechsel ist weiterhin kein legaler
Renewal; der korrigierte Test prueft diese Ablehnung und anschliessendes Leave.

Legacy-Transport/Service und das alte Integration-v1-Feld
`publisherConsentRequired` bleiben als Kompatibilitaetsschuld erhalten; die aktive
UI interpretiert es nicht als aktuelle Freigabeanforderung. Private Audio-/Visual-
Testbruecken stoppen reale synthetische Quellen fuer Entzug. Selektiver Multi-
Worker-Widerruf muss vom owning Hub kommen: Der obsolete negative Consent-
Brueckenbefehl endet bis zu dessen Fixture-Anschluss begrenzt mit
`test_hub_receive_revocation_required`. Er stellt keinen erfolgreichen Entzug dar.
Voller Clientblock, aktuelle gepinnte Worker-/Browser-Gesamtkomposition, Live-
Provider/GPU und Registry-Release-/Deployment-Abnahme bleiben offen. Aktueller
Nachweis: `artifacts/test-gates/meet-receive-policy-migration.v1.json`.


Der aktuelle gekoppelte Hub-/Worker-Test hat eine weitere Legacy-Luecke
geschlossen: Der Signaling-Backchannel lieferte nur alte Consent-Eintraege
(Revision 0, leere Quellenliste), waehrend der Browser bereits Hub-Receive-Rechte
benutzte. Eine kleine reine Serverprojektion stellt jetzt aktuelle
Hub-Capabilities, authentifizierte menschliche Membership/Publikationen und
Lease-Deadline im kompatiblen Quellenformat dar. Sie erteilt keine Senderechte
oder neue Lease. Der Lease-Store liefert die aktuelle geschlossene Lease an
seinen registrierten Port und prueft Liveness davor und danach.

39 Node- und 18 Hub-Regressionsfaelle bestehen. Das frisch gebaute eigenstaendige
Workerimage besteht die Quellcode-/Dependency-Pruefung. Zwei private reale
Worker mit gepinntem Playwright 1.58.0, realer Hub-SQL/TaskQueue und aktuellem
Client bestehen zwei korrelierte Antworten, gleichzeitige synthetische Sprache,
unterschiedliche Avatare, unabhaengigen Bildwiderruf und gezielten Hub-Stopp
einer Teilnahme bei weiterlaufender zweiter Teilnahme (67.63 s nach Fix). Der
vorherige fehlgeschlagene Lauf wird im Bericht erhalten. Der vorhandene Hub-
Task-Cancel ist der selektive Widerruf; ein neuer Legacy-Consent-Befehl ist
dafuer nicht noetig. Dies ist eine Dialog-Runtime-Abnahme mit synthetischer
Inferenz und keine Pruefung des Legacy-Companion-Gesamtloops unter langem
Modell-/Medienrueckstau. Live-Provider/GPU, Deployment und produktive Registry-
Freigabe bleiben offen. Bericht: `artifacts/test-gates/meet-hub-receive-projection.v1.json`.


Der anschliessende volle Clientblock `npm run check` ist noch nicht bestanden:
187 Frontenddateien/1694 Tests bestehen, vier Dateien/19 Tests schlagen fehl
(SFrame-Key-ACK/Overlay-Testdoubles, Avatar-Setup/Reason/Clock), plus eine
unhandled Rejection. Die spaeteren Build-/Leakage-/Go-/Node-/Infrastruktur-
Stufen wurden nicht erreicht. Diese konkreten Regressionen sind im aktiven
Track festgehalten und muessen untersucht und korrigiert werden; der erfolgreiche
gekoppelte Teilnachweis wird nicht als gruener Gesamtblock ausgegeben.

## Avatar-/SFrame-Blockregression

Der Client setzt die bereits autorisierte Grenze von 120 s Aktivierung und
110 s Setup konsistent um. Er prüft die aktuelle Autorität vor Readiness und
nochmals vor dem ersten Frame/Receipt. Der 2,5-s-Controller-Heartbeat beginnt
erst nach dem Receipt; er verlängert keine Aktivierungsfrist. Diagnoseausgaben
enthalten keine vollständigen Autoritäts-, Peer-, Schlüssel- oder Publication-
Metadaten mehr. Kryptografische Prüfungen bleiben erhalten.

80 gezielte Regressionstests und der vollständige Frontendblock mit 1.719 Tests
sind bestanden, ebenso Produktionsbuild, Leakage-Prüfung und vier Go-Module.
Die vollständige Node-/Browser- und Infrastrukturprüfung läuft noch; daher ist
der Gesamtblock noch nicht als bestanden bewertet. Bericht:
`artifacts/test-gates/meet-client-block-regression.v1.json`.

SRP/ISP/DIP: Avatar-Lifecycle und Autorisierungsport bleiben getrennt. Die
bestehende große PeerMeshService-Klasse behält dokumentierte SRP-Schulden; diese
Änderung fügt keine neue Kopplung oder Hub-/Worker-Orchestrierung hinzu.

Im laufenden Gesamtcheck sind Firefox-Multi-Publisher und natives Audio-Unmute
fehlgeschlagen. Die Multi-Publisher-Fixture beendet jetzt ihre eigene Avatar-
Generation ausdrücklich vor dem Wiederöffnen: Ein 2-s-Wait konnte den echten
2,5-s-Heartbeat nicht zuverlässig abwarten, und Membership allein beendet nach
dem aktuellen Vertrag nicht jede Avatar-Generation. Chromium/Firefox bestehen
im gezielten Nachlauf (2 Tests, keine Skips). Der native Audiofall besteht
isoliert ohne Codeänderung; der Quellenverlust im Gesamtcheck bleibt ungeklärt
und wird nicht als behoben gewertet.

Der erste vollständige Nachlauf ist terminal fehlgeschlagen: 1.790 Node-/Browser-
Tests bestanden, zwei fehlgeschlagen, fünf übersprungen; die Infrastrukturstufe
wurde nicht erreicht. Die späteren gezielten Nachläufe ersetzen dieses Ergebnis
nicht. Eine ausschließlich lesende, auf 32 Einträge begrenzte Fixture-Beobachtung
projiziert jetzt Lease-Revision, relative Fristen und feste Stop-Codes ohne IDs,
Tokens oder Quelleninhalte. Zwei Unit-Tests und der reale Audio-Nachlauf mit
dieser Beobachtung bestehen. Ein zweiter vollständiger Blockcheck läuft.

## Begrenzte Browserdiagnose im Companion

Renewal-Callbacks lesen keinen Response-Body mehr; sie speichern ausschließlich
den validierten HTTP-Status. Browsermeldungen und Beobachtungsfehler erzeugen
feste Ereignis-/Schweregradmarker statt roher Meldungen oder Exception-Texte.
Neun Tests prüfen die tatsächlichen Callback-Funktionen, einschließlich eines
Response-Doubles, dessen Body niemals gelesen werden darf. Die zusammenhängende
Inbox-/Pump-/Composition-Regression besteht mit 47 Tests; sechs explizit
bundleabhängige Endpoint-Fälle sind übersprungen. Worker-Boundary-Gate und Ruff
bestehen. Bericht: `artifacts/test-gates/meet-companion-browser-diagnostics.v1.json`.

Die Projektion bleibt im Diagnosemodul (SRP); keine neue Hub-Autorität oder
Orchestrierung entsteht. Der große `run()`-Composition-Pfad und das globale Log
bleiben bestehende SRP-/DIP-Schulden. Nach dieser Worker-Quelländerung muss vor
der nächsten gekoppelten Abnahme ein neues unveränderliches Image gebaut werden.

Der anschließende Raum-/Join-Abgleich entfernt auch ungefilterte Peer-/Chat-
Snapshots sowie Raum-/Invite-URLs aus den Diagnoseausgaben. Die vorgesehene
Datei `room.json` behält den tatsächlichen Raumlink; die Discovery-/Rejoin-
Funktion bleibt erhalten. Public-Room-Fehler erlauben nur bekannte feste Codes
und validierte HTTP-Statuswerte. 62 Regressionstests bestehen, sechs
bundleabhängige Endpoint-Fälle sind sichtbar übersprungen; Ruff und das
Worker-Boundary-Gate bestehen. Weiterhin keine produktive Release-Evidence.

Die anschließend frisch gebaute Standalone-Worker-Verpackung besteht den
headless Container-Nachlauf (1 Test, 2,16 s). Zwölf exakte Quellmodule, darunter
Diagnose- und Inbox-Processing-Port, stimmen mit dem aktuellen Workspace
überein. Runtime-Lock, persistenter Modell-Pipe, Default-Entry-Verweigerung,
CPU-Medienvorbereitung und Cancellation sind geprüft. Der Probe-Container ist
unprivilegiert, ohne Netzwerk/GPU/Hub-Paket und mit schreibgeschütztem Root
ausgeführt und danach entfernt. Das lokale Image bleibt für die noch fehlende
volle Companion-Browser-Abnahme verfügbar. Bericht:
`artifacts/test-gates/meet-companion-diagnostics-packaging.v1.json`.

Der gepinnte Chromium startet im aktuellen unveränderlichen Worker-Image mit
aktivierter Chromium-Sandbox, unprivilegiertem Nutzer, schreibgeschütztem Root,
`no-new-privileges` und dem bereits vorgesehenen `chromium-seccomp.json`.
Browser-Kontext und synthetische Seite sind headless geprüft (1 Test, 2,05 s).
Das Docker-Standardprofil scheiterte im ersten Probeversuch; das produktiv
vorgesehene Profil besteht ohne Änderung oder Sandbox-Bypass. Dies beweist den
Browser-Setup-Schritt, noch nicht den vollständigen `Companion.run()`-Loop mit
Hub-Grants, Chat-Rückstau, Renewal und Medienvorbereitung.

Der echte Hub-Endpunkt `/internal/companion`, `MeetTurnService` und der
Ed25519-Issuer sind gemeinsam mit Worker-HMAC geprüft (5 Tests, 2,56 s).
Erstaufnahme und Renewal bewahren Tenant/Project/Task/Runtime/Session/Room,
erneuern `jti` und bleiben zeitlich begrenzt. Entzogener Scope, Worker-Rolle,
falsche Signatur und fehlender Issuer verweigern den Grant. Der Policy-Port ist
ausdrücklich synthetisch; keine Registry-Release-Evidence entsteht. Die Prüfung
setzt keine Worker-/Task-Ausführungsports ein und beweist noch keinen
vollständigen Browser-/Companion-Loop. Bericht:
`artifacts/test-gates/meet-companion-hub-grant-contract.v1.json`.

Der zweite vollständige Client-Blockcheck ist abgeschlossen (`npm run check`,
Exit 0): 1.719 Frontend-Tests und 1.795 Node-/Browser-Tests bestanden, keine
Fehler. Fünf Node-Tests sowie 14 Live-Infrastrukturgates sind sichtbar
übersprungen. Build, Leakage-Prüfung und vier Go-Module bestehen. Das lokale
Client-Regressionspaket ist damit `done`; aktuelle Zwei-Worker-Kopplung, voller
Legacy-Companion-Loop und produktive Abnahme bleiben separat nachzuweisen.

Die erneute Hub-/Zwei-Worker-Persona/Speech-Abnahme mit dem frisch gebauten
Image ist fehlgeschlagen (1 Test, 43,25 s) bei `media-avatars`, vor den beiden
Chatantworten. Der Bridge meldet einen generischen begrenzten Fehler, keinen
Proxy-Verbindungsabwurf; die Ursache ist noch zu diagnostizieren. Der getrennte
volle Client-Check bleibt bestanden. Frühere gekoppelte Erfolge gelten nicht
als Nachweis für diesen aktuellen Image-Lauf. Bericht:
`artifacts/test-gates/meet-current-two-worker-image.v1.json`.

### Current Worker/client avatar contract recheck (2026-10-11)

The current client activation lasts at most 120 seconds (600 frames at five fps),
with bounded 110-second setup. The Worker had retained the older 30-second /
150-frame / 10-second contract and rejected current receipts. Shared execution
constants now align these bounds while preserving the exact Hub deadline and
2.5-second authenticated-update watchdog. Old shorter receipts remain valid.
This is an execution-contract change; Hub authorization and task ownership stay
unchanged. Shared constants avoid duplicated validation limits (SRP/OCP); the
existing browser execution port preserves DIP.

A fresh standalone image passed exact-source checks for 14 modules and the
actual Hub/two-Worker persona-speech gate (68.68 seconds). Earlier failed image
checks remain recorded in `artifacts/test-gates/meet-current-two-worker-image.v1.json`.
These are synthetic local technical observations, not production release evidence.
The full legacy `Companion.run` backlog gate remains separate and unverified.

### Actual packaged legacy Companion.run acceptance

`tests/meet/test_meet_companion_live.py` runs the existing production `run()`
in the standalone image against the actual Flask Hub grant endpoint and current
Meet server/receiver. The first synthetic model answer takes 50 seconds while
three human messages await processing. Real Ed25519 grants and Worker HMAC,
sandboxed nonroot Chromium, closed process transport and CPU FFmpeg snake media
remain active. The decoded-media case passed in 64.64 seconds: three unique
replies, at least three signed Hub grants, decoded avatar and non-silent remote
audio, no human captures, transform errors or proxy drops, and bounded clean stop.
The final case passed in 64.48 seconds and additionally asserts exactly three
RECV/REPLY markers, no gaps or reply/control errors and at least two successful
browser renewal responses.

Opt in with `MEET_COMPANION_LIVE_GATE=1`, an explicit immutable
`MEET_COMPANION_PACKAGED_IMAGE`, a current `MEET_TEST_PUBLIC_DIR`, and Node on PATH.
The bridge and probe are focused private test infrastructure, not product routes.
Only the model/TTS process uses a synthetic answer/tone double; no live provider,
GPU quality or production Registry evidence is claimed. The private probe mounts
no Hub package or host browser profile. Key loading retains owner-only policy.

This proves the bounded legacy execution slice; revoked-source/recovery and
32-message pressure remain separate actual-loop cases. It does not resolve the
legacy companion grant's bypass of ordinary Hub task admission. That preserved
architecture debt and the broad `run()` composition must be migrated separately,
with the Hub continuing to own task assignment and authorization.

### Full bounded backlog/Hub-policy lifecycle matrix

The current standalone image passes the 15-module packaged source gate and
four actual legacy loop cases: ordinary three-input backlog, Hub-policy
withdrawal during the first model round, 32-input pressure, and explicit
Hub-policy restoration followed by a test-owned Worker restart. All five
container checks pass together in 241.88 seconds. The live model is still the
explicit synthetic 50-second-first-answer process, with real browser, Hub
signing, CPU media and encrypted receiver observation. All 67 ledger/inbox/pump/
composition checks pass with the current freshly bundled real chat endpoint.

The first 32-input attempt delivered every reply but emitted 14 false gap
markers. `ChatLedger.observe` now reconciles the validated pending inbox IDs
(maximum 32), keeping delivery distinct from model admission and answers.
Queued entries remain NEW for execution; browser source policy, ACK, freshness
and reply-window rules are unchanged. The optional keyword preserves older
callers, with ledger bookkeeping separate from execution and authority (SRP).

Actual withdrawal after the model-start marker makes the Hub deny renewal;
the parent stops its model process, sends no answer, leaves with exit zero and
removes the remote camera within the test's 30-second budget. The initial
observation timeout came from a participant counter hidden by the Chat view;
Live-view observation verifies departure without relaxing security. Explicit
Hub restoration plus a private test-owned restart admits only new inputs,
with no replay of the three retired questions. This is not automatic Worker
reauthorization or orchestration.

Individual human-source withdrawal, configured model/TTS/GPU/Lipsync, normal
Hub Task admission migration of the legacy grant, versioned transport cleanup
and productive deployment/Registry evidence remain separate work. Earlier
failed observations remain recorded in
`artifacts/test-gates/meet-companion-pressure-recovery.v1.json`.

### Actual single-human source withdrawal

The `source-revoke` case now passes against the same immutable image in
20.24 seconds. It waits for the actual synthetic model process to start, then
the authenticated human leaves the real room. Hub scope/capability policy stays
enabled and no Hub renewal is denied. Actual membership loss makes the browser
reject reconfirming the retired source: the Companion stops without a reply,
closes its owned model process, leaves server membership and exits with zero.
The private probe exports fixed counters before exit (one RECV, zero REPLY);
container tmpfs cannot be read afterwards. The observation is bounded to
30 seconds and does not introduce per-peer consent or a Worker policy bypass.

This is a separately run source-withdrawal case, not a claimed combined six-case
batch or multi-human continuity guarantee. The prior five-case packaged/lifecycle
matrix remains recorded separately. Normal Hub Task admission migration,
configured providers/GPU, versioned cleanup and productive Registry evidence
remain open. Details: `artifacts/test-gates/meet-companion-source-withdrawal.v1.json`.

### Normal Hub Task migration foundation

`agent/services/meet_companion_task_grants.py` adds an assignment-bound grant
port over the existing `MeetDialogAuthority` and real signer. It cannot create
a task or invent a dispatch/runtime/session identity. Every request must name
the exact current Task, dispatch lease, runtime, project, Hub session and closed
capability set. The signer performs its existing authority reread through a
bound adapter; cancellation and rebinding deny the grant. A copied initial
snapshot and post-signing reread also fence changed controls before returning.
Fourteen new contract cases pass, with five legacy HTTP/signing cases retained.

The small authority/signer ports keep policy projection separate from dispatch
(SRP/ISP/DIP) and reuse existing policy checks (OCP). This foundation is not yet
bootstrap-composed, exposed over HTTP or consumed by the executable Companion.
Normal Task admission/dispatch, closed Worker assignment, ongoing Hub controls,
freshness/cancel/result tracking and actual SQL/container acceptance remain
required before the Legacy runtime migration can be considered complete.
No deployment, compatibility switch or production Registry claim is made.

### Task-bound Hub HTTP composition

The separate `POST /api/meet/v1/internal/companion/task-grant` callback now
composes the Task grant port when `ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED=1`
in the existing enabled dialog Hub composition. The default is off; missing
service/key and Worker-role calls fail closed. This flag exposes only the grant
foundation, not a migrated Companion runtime or new task admission workflow.

The callback requires bounded JSON (1536 bytes), the dedicated
`X-Ananta-Companion-Task-Signature`, and a closed envelope containing schema
`ananta.meet-companion-task-grant-request.v1`, a 32-hex RPC nonce, integer
`sent_at`, and the exact closed `assignment`. The Hub accepts at most 10 seconds
of request age and 3 seconds of future clock skew. This nonce is request
correlation, not a Hub Evidence Registry identity. Direction-separated response
HMAC binds the exact request (including nonce) and bounded response/controls
(8192 bytes). Duplicate keys, unknown envelopes, bearer/query/transfer-encoding
inputs and legacy signature reuse are rejected with no legacy fallback.

All 37 service/actual HTTP/bootstrap/legacy compatibility checks pass in
2.88 seconds. Route auth/bounds, grant policy and bootstrap remain separate
(SRP), using existing narrow authority/signer ports (ISP/DIP). Worker response
verification, dispatch/profile integration, ongoing control/cancel/result
handling and persisted SQL/container acceptance remain required.

### Task-bound Worker transport

`AssignedCompanionGrantClient` now consumes the separate Task grant callback
using only a copied closed Hub assignment and operator-fixed endpoint/key.
Each request has a fresh RPC nonce, separated HMAC and integer timestamp; the
response must match the request signature, original Task/dispatch/runtime/
session/project/room/capability scope, bounded deadline and closed source
controls. The transport returns within a 2.5-second freshness budget and never
extends its original monotonic assignment deadline. Audio/screen and unassigned
optional controls cannot be introduced into the closed Companion profile.

The default opener ignores environment proxies and rejects redirects. Actual
loopback HTTP verifies success, redirect denial without reaching the target,
and cancellation denial. Replayed, unsigned, oversized, changed-scope and late
responses fail with a fixed error, with no cached or legacy authority fallback.
All 60 service/route/Worker/legacy checks pass in 3.12 seconds (23 Worker cases).
A fresh standalone image passes its 17-file source gate in 1.90 seconds and
imports/constructs the client without Hub package, profiles or network access.
This package gate does not claim an actual Hub network fetch inside that image.

Transport, pure wire validation and future control execution remain separate
(SRP); injected HTTP/clock ports provide deterministic tests (DIP). This is a
verified migration transport, not executable Companion migration: normal Hub
profile admission/dispatch, ongoing source controls/cancel/result processing
and persisted Task/SQL/container acceptance remain required.


### Assigned Companion control boundary

`CompanionTaskControls` consumes the signed assignment-bound client through a
single-flight read exchange. Current observations expire 2.5 seconds after the
request starts; response latency consumes that budget. A source checkpoint
binds its local execution owner, source revision and activation timestamp.
Hub pause/resume invalidates old operations without invalidating unrelated
source checkpoints. Stale state, denial or regressed controls closes the gate.

74 contract/client/route/control regression cases and two real loopback HTTP
cases pass. In the HTTP cases, actual Hub cancellation or chat pause interrupts
a running owned synthetic model within three seconds; no fallback result is
returned. Task storage is synthetic, not persisted SQL. The fresh standalone
image verifies 18 source files and denies execution before any Hub observation.
These are local technical observations, not production release evidence.

Control reading/fencing, transport and media execution remain separate (SRP,
ISP, DIP). The executable legacy Companion loop does not yet consume this gate;
normal Hub profile dispatch, source-specific pause/resume, result/terminal
tracking and persisted SQL/container acceptance remain required. The existing
broad Companion composition and autonomous non-task supervisor remain debt.
See `artifacts/test-gates/meet-companion-task-controls.v1.json`.


### One-assignment Companion runtime

`companion_task_runtime.run(client, log=...)` now implements an assigned browser
run separate from the legacy supervisor. It joins only the signed room, renews
only the same assignment, and never admits tasks or rejoins after loss.
`AssignedCompanionExecution` fences chat model work and sends under current
source checkpoints. Paused optional speech cannot retract a delivered chat
answer, and failed chat delivery does not start speech. The browser adapter
closes avatar/audio independently, fences prepared avatar media before opening,
and refreshes idle avatar/pulses only under current Hub observations. Empty
permissions, human capture denial and fixed-origin browser network rules apply.

83 related regression cases pass, including actual synthetic model processes
and signed loopback HTTP cancellation. Three browser-adapter tests use doubles;
a full assigned browser run is not yet verified. A fresh standalone image
passes the 20-file package gate. The existing legacy composition regression
is checked separately. These are synthetic local technical observations,
not production release evidence.

Execution, browser adaptation and authority reads protect SRP/ISP/DIP. The
legacy broad run/supervisor remains unchanged debt. Normal Hub profile
admission/dispatch selection, progress/result/terminal lifecycle reporting,
persisted SQL and actual-container browser acceptance remain open. Assigned
speech currently plays independently of the idle avatar; synchronized speaking
clips remain required before feature parity is claimed. See
`artifacts/test-gates/meet-companion-assigned-runtime.v1.json`.


### Assigned runtime entrypoint and lifecycle ports

`python -m worker.meet_media.companion_task_runtime` reads the bounded closed
assignment from stdin and closes that descriptor before creating browser/model
children. It uses the existing Worker key and operator-bound Hub URL, and
reports terminal status through the existing authenticated dialog `finish`
callback. Control-loss exceptions still run cleanup/terminal reporting. The
fixed assignment duration bounds the run; there is no supervisor or rejoin.

The inherited parent progress channel reports only after a new valid Hub
observation, using freshness anchored to request start. Local browser/model
ticks cannot renew parent progress. 77 runtime/control/process/HTTP/progress
cases pass; the fresh 20-file packaged gate also checks actual CLI malformed-
assignment rejection with exit 1 and empty stdout/stderr. These are synthetic
local observations, not production release evidence.

Normal Hub admission/profile binding and signed executor selection, actual
parent-spawn/progress acceptance, full browser/SQL/container execution and
synchronized speaking clips remain open. Existing narrow lifecycle/progress
ports preserve SRP/ISP/DIP; the legacy broad composition remains debt. Report:
`artifacts/test-gates/meet-companion-runtime-terminal.v1.json`.


### Normal Hub admission and signed executor selection

The existing dialog start API accepts the optional closed field
`companion_execution: true` only when Hub Task-grant composition is enabled
with `ANANTA_MEET_COMPANION_TASK_GRANTS_ENABLED`. It is disabled by default.
Existing project/parent authorization and normal Task admission apply. The Hub
persists the execution profile before dispatch, includes it in the signed
assignment and phase binding, and exposes it in status. Absent negotiation
preserves existing dialog behavior and phase digests. Unsupported receive,
profile-selection and recovery features are rejected for this runtime.

Current authority and router reject removal/replacement of the bound profile.
The Task-grant port rejects normal dialog Tasks as Companion authority, and
the Worker client rejects unnegotiated assignments. `DialogExecutor` chooses
only one of the fixed installed dialog/Companion modules after closed-envelope
validation, retaining existing replay, slots and parent watchdog. No arbitrary
command or Worker admission is introduced.

183 related tests pass, including real SQL Task profile admission and dispatch
port checks. The fresh standalone image passes its 22-file gate. Actual
executor-child/browser/progress/terminal and complete SQL runtime acceptance
remain pending, along with joined/publishing phase observations, synchronized
speaking clips and provider/GPU/rollout acceptance. No deployment occurred and
these synthetic local observations are not production release evidence.
Closed profile validation stays separate from the broad admission/authority
services (SRP/ISP/DIP); their existing size remains documented SRP debt. Report:
`artifacts/test-gates/meet-companion-profile-dispatch.v1.json`.


### Actual assigned browser and parent process acceptance

The private assigned-mode browser gate now passes two actual container runs:
three unique replies after a 50-second synthetic model operation with decoded
avatar/nonzero audio, and Task cancellation during that operation with zero
replies, camera/membership removal and owned model termination. Both runs use
only the Task-grant endpoint, never legacy Companion grants. There are no
observed device captures, transform errors or proxy drops. The missing private
fixture CA for Playwright's Node HTTP route transport was added through the
existing `NODE_EXTRA_CA_CERTS` test configuration; production trust was not
changed. Earlier setup failures remain recorded. Two cases pass in 72.66 s.

Eleven actual executor/parent watchdog regression cases pass in 11.74 s:
completion, current Hub cancellation, independent stalled-child process-group
kill, slot release and lease replay refusal. These process cases use an
explicit synthetic run adapter and deterministic terminal port. The separate
container gate now matches 27 source files including executor/watchdog/progress
and constructs the replay adapter without Hub package, network or writable root.

The browser cases use real browser/media but synthetic Hub Task storage and
direct runtime invocation. SQL admission and parent execution were verified
separately; a combined persisted SQL/signed executor/browser/terminal run is
still required. Assigned phase observations, synchronized speaking clips and
provider/GPU/production rollout acceptance remain open. These are synthetic
local observations, not production release evidence. Small execution/control/
watchdog ports preserve SRP/ISP/DIP; legacy broad composition remains debt.
Report: `artifacts/test-gates/meet-companion-assigned-acceptance.v1.json`.


### Phase-enabled Companion admission correction

The SQL admission regression now includes the regular Hub phase coordinator.
It exposed a missing Companion profile in the reconstructed expected-scope
digest: a persisted profile could previously fail the admitted transition.
The coordinator now reuses the closed optional profile projection, preserving
legacy absent-field digests. The new SQL test failed before the fix and now
verifies admission, joined phase, coordinator restart, publishing observation
and rejection of profile removal. Publication observations are deterministic
test doubles; this is not combined actual-browser/SQL acceptance.

71 related phase/admission tests pass. Hub phase ownership and current authority
checks are preserved; no Worker phase claim becomes authorization. The fix
protects SRP/ISP/DIP by reusing focused projection rather than new inline policy.
Existing broad services remain debt. Automatic assigned-runtime phase reads,
combined SQL/executor/browser acceptance and synchronized speaking clips remain
open. Report: `artifacts/test-gates/meet-companion-phase-binding.v1.json`.


### Hub-verified assigned phase observation

The additive signed `observe` callback accepts only normal assignment IDs, a
fresh nonce and the existing Meet session ID. The Hub reads membership and
publications through its trusted Meet observation port and rechecks current
Companion Task authority before persisting joined/publishing metadata. Caller
phase/publication fields are rejected; an observation grants no new rights.

The assigned entrypoint pins its joined Meet lease and polls observations with
the existing single-flight exchange on a background HTTP thread. Browser
objects stay on their owner thread. HTTP has a 2.5-second budget; observation
failure becomes control loss and cannot generate a fallback answer. Parent
progress continues to derive only from current Task-grant controls.

52 SQL/phase/runtime/parent/transport regressions and one actual signed HTTP
case pass. A fresh standalone image verifies 30 source files. The earlier
actual-browser acceptance belongs to the previous image; the new observation
path still requires combined SQL/executor/browser/terminal acceptance on the
current image. Synchronized speaking clips and provider/GPU/rollout acceptance
remain open. These synthetic local checks are not production release evidence.
Focused observation/verification/persistence ports protect SRP/ISP/DIP; broad
legacy services remain debt. Report:
`artifacts/test-gates/meet-companion-phase-observation.v1.json`.


### Atomic phase observation validation

An additional SQL negative case exposed a partial metadata write: an expired
publication receipt could persist joined membership before the publication
transition failed. The coordinator now builds and validates both transitions
in memory and applies the complete observation through one existing Task CAS.
An invalid receipt leaves connecting unchanged. A separate SQL case cancels
the Task during Meet I/O and verifies that no membership is persisted afterward.
71 related regressions and that additional cancellation case pass. The fix
reuses the phase repository port (SRP/DIP/OCP); no Worker authority or Task
orchestration was added. Combined SQL/executor/browser/terminal acceptance
and synchronized speaking clips remain open. These are local synthetic
observations, not production release evidence. Report:
`artifacts/test-gates/meet-companion-phase-atomicity.v1.json`.


### Combined SQL Task and executor callback acceptance

Four isolated SQL cases now connect normal Hub admission to an actual executor
child, current signed HTTP Task grants and the actual Hub terminal service.
Completion terminalizes the SQL Task; Hub cancellation stops the child and
a later failed finish cannot overwrite cancelled state. The parent releases
its slot, and replaying the exact admitted lease cannot launch another child.
The phase-enabled variants also send the signed observation callback through
the actual child and persist Hub-validated membership in SQL, on both completion
and cancellation. The physical Meet observation is a declared synthetic trusted
port fixture. All four cases pass in 10.10 seconds; the earlier 19 related
regressions passed in 15.72 seconds. The added replay check waits
boundedly for asynchronous parent slot release, rather than racing that release.

Worker dispatch HTTP is replaced by an explicit local start_dialog adapter;
browser execution remains a declared synthetic run adapter. This does not
prove the combined actual-container/browser path. That path, synchronized
speaking clips, assignment-bound CodeCompass/tool policy parity and configured
provider/GPU/rollout acceptance remain open. Hub owns persisted Task lifecycle;
test dispatch/resource ports preserve SRP/ISP/DIP. Existing broad legacy
composition remains debt. These local synthetic observations are not release
evidence. Report: `artifacts/test-gates/meet-companion-sql-executor.v1.json`.


### Assigned model configuration and legacy tool authority

The installed assigned profile grants chat/generated media, without Hub tool
execution. The model child previously inherited the legacy Companion's
default-on CodeCompass/tool flags. A dedicated assigned environment projection
now disables CodeCompass/tools/RAG-prefix regardless of parent settings,
removes Hub endpoint/key-file configuration, and binds the project to the
closed assignment. Local model/provider and TTS settings are retained; parent
configuration and legacy behavior stay unchanged.

An actual child constructs and exercises the real CompanionDialog without
a toolbox/retriever, with forbidden Hub assist stubs proving no lookup occurs.
14 environment/runtime/execution cases pass; the fresh standalone image checks
31 source files and the configuration projection. This filters supported
execution ports; it is not a filesystem/network sandbox or mounted-credential
isolation proof. Assignment-bound authorized CodeCompass/tool integration is
still needed for feature parity. Current combined SQL/executor/browser/phase/
terminal, synchronized clips and provider/GPU/rollout acceptance remain open.
Focused configuration projection protects SRP/ISP/DIP; inherited broad legacy
composition remains debt. No production release evidence is claimed. Report:
`artifacts/test-gates/meet-companion-model-profile.v1.json`.


### Combined actual SQL, Worker and browser acceptance

The earlier combined integration gap is now closed by three actual private
container cases. Normal Hub SQL admission and control CAS dispatch through the
signed Worker HTTP endpoint and installed executor/Companion entrypoint. The
sandboxed browser joins the actual private Meet room; the Hub obtains its
membership/publication observation over the real TLS backchannel and persists
it in SQL. Only ephemeral policy/project/room-binding fixtures, model/TTS
responses and the private certificate trust seam are synthetic.

Three unique replies survive a 50-second model call, with decoded avatar video
and nonzero synthetic speech audio. Hub cancellation during the model produces
no replies, removes camera/membership, stops the owned model and frees the actual
executor slot. A separate 90-second Task completes at its original deadline;
the actual signed terminal callback persists completed in SQL. No legacy grant
endpoint is called. All three cases pass in 188.01 seconds, alongside 38 related
regressions and a fresh 31-file standalone image check.

The combined run exposed and fixed two short-duration errors. A browser lease
already reaching the original Task deadline cannot be strictly extended, so the
assigned browser adapter skips that unnecessary renewal after a fresh signed
Hub read. Current Hub control reads continue. Designated control/membership
loss at the original monotonic deadline ends the planned duration normally;
earlier loss still propagates, and the Hub preserves its own cancelled state.
No execution window or capability is extended. The narrow duration loop and
browser renewal adapter preserve SRP/ISP; Hub ownership is unchanged. Broad
legacy composition and global SQL/singleton coupling remain SRP/DIP debt.

Assigned synchronized speaking clips, assignment-bound CodeCompass/tool policy
parity and configured provider/GPU/lipsync/production rollout acceptance remain
open. The default legacy runtime has not been productively migrated. These are
local synthetic technical observations, not Registry-backed release evidence.
Report: `artifacts/test-gates/meet-companion-integrated-container.v1.json`.

### Assigned synchronized speech and independent source controls

The assigned runtime now composes the existing synchronized clip publisher
through a focused utterance controller. Speech and avatar have separate current
Hub checkpoints. Disabled avatar skips rendering; avatar retirement is sticky
for an utterance, so resuming cannot publish old prepared clips. Speech pause
interrupts remaining PCM frames while a still-authorized idle avatar can return.
Skipped or retired clips are explicitly incomplete synchronization observations.
Initial PCM timing is anchored after initial clip/status setup; later stalls
retain the existing bounded scheduling checks.

66 focused regressions pass. The fresh standalone Worker checks 32 unique source
files. Actual SQL/Worker/browser cases verify the three original task scenarios
and separate avatar-paused/audio-paused scenarios. Audio-only reception uses
existing peer/source/publication metadata; it requires no camera or additional
capture. Two additional actual-container cases pass in 151.29 seconds: pausing avatar
during audible speech removes camera without stopping audio; pausing speech
stops remaining PCM frames while avatar remains. These seven scenarios were
verified across separate runs, not a single all-green matrix. Sender publication
drift is not remote perceptual synchronization.

The focused controller and optional publisher predicate protect SRP/ISP/OCP
and preserve the legacy API. Broad legacy composition, loosely typed callable
ports and global SQL/singleton coupling remain explicit SRP/DIP debt. The earlier
synchronized-clip implementation gap is superseded by this section. Task-bound
CodeCompass/tool policy and Registry evidence parity, configured provider/GPU/
lipsync and production rollout remain open. The default runtime is not migrated;
all observations are local synthetic checks, not production release evidence.
Report: `artifacts/test-gates/meet-companion-task-speech.v1.json`.

### Task-bound CodeCompass transport and Registry admission

`POST /internal/companion/task-tool` is a separate default-off Hub port,
configured through `ANANTA_MEET_COMPANION_TASK_TOOLS_ENABLED=1` only alongside
Task grants. It checks the exact current Task/lease/runtime/session/capabilities,
chat activation, operator read policy and sealed source scope. Queued work must
pass another current check at actual execution start; changed authority discards
its result. No request supplies an evidence identity.

Separate Hub SQL repositories enforce a durable 240-attempt assignment budget
and nonce replay protection, and immutable tenant/project-scoped input/output
admission. The Hub Evidence Registry issues source identities after admission,
reserves a tool run before execution and records its exact success/failure. Source
content is persisted as bounded Hub evidence input/output; it is not Worker state.
The Worker client consumes the validated grant transport and original deadline,
checks request-bound signatures and closed run/source projections, and has no
legacy endpoint fallback. 145 regressions and a 35-unique-file standalone image
probe pass. An earlier speech-only count included a duplicate path: that image
verified 32 unique files, corrected in its report.

This closes the transport/policy/Registry foundation, not full feature parity.
The model process still disables legacy tool/RAG environment ports. Its toolbox
integration and separate pre-reserved model run are pending. Architecture
intelligence and layer heads/plan currently ignore sealed capabilities; the new
port explicitly denies those three handlers until scoped adapters exist. The
other five tools use their existing capability-aware MCP handlers, including
retrieve, with no global knowledge-index fallback.

Separate attempt/source ports protect SRP/ISP; shared validation extraction
reduces route duplication. Hub ownership and Worker packaging remain unchanged.
Global MCP handlers, shared executor/service locators and loosely typed payloads
remain explicit coupling/containment debt. Already-running read work is not
thread-cancelled on timeout; no provider/hardware attestation or production
release claim is made. Report:
`artifacts/test-gates/meet-companion-task-tools.v1.json`.

### Scoped architecture intelligence and exact graph boundaries

The three-handler gap above is now reduced to the two layer handlers.
Architecture intelligence validates the sealed capability and uses the actual
selected authorized index as its snapshot reference. Foreign snapshot/revision
requests fail before loading a graph. Only permitted relative-path nodes and
edges between retained nodes reach analysis.

The source audit also exposed two older graph weaknesses. Consumption
compatibility could admit a legacy index outside an explicit allowed-index
list; the resolver now filters exact IDs before artifact resolution, including
legacy rows. An empty explicit scope resolves no graph. Architecture path
matching formerly accepted sibling prefixes such as `agent-secret` under
`agent`; a shared segment-bounded permission function also excludes traversal.
Task-tool execution binds its verified capability around the actual MCP call so
legacy graph consumers see that exact scope, then restores the previous context.

160 regressions pass, including six focused graph-scope cases, three through
actual Task-tool HTTP/MCP with SQL Registry admission. Task/index/artifact admission fixtures are explicitly
synthetic; parser, MCP analysis and persistence are actual. Backend errors are
bounded coded refusals. No productive source-origin migration, model-answer
Registry grounding or release evidence is claimed. Worker files/image are
unchanged by this Hub graph slice.

Focused graph projection/shared permission checks protect SRP/OCP. The injected
resolver seam protects testability; existing global repository/artifact/policy
facades preserve DIP coupling debt. Injecting those three ports into a resolver
object is the cleaner future structure. Layer heads/plan, owned model-tool
integration, a separate pre-reserved model run and combined runtime acceptance
remain open. Report: `artifacts/test-gates/meet-companion-scoped-graph.v1.json`.


### Assigned Task layer heads and plan scope

The historical layer-handler gap described above is now closed at the Hub
Task-tool port. All eight read-only handlers have scoped adapters. Layer heads
resolve only explicitly authorized, completed Hub pointer indices. Plans use
actual stored snapshots, filter file changes to permitted relative paths, and
reject model-supplied replacement manifests and concurrent head changes. The
existing context-free APIs remain compatible. No build, dispatch or publication
operation is exposed by the scoped query.

Seven actual HTTP/MCP tests exercise real layer/head/snapshot stores with
explicit synthetic Task/index/policy fixtures; the combined regression has
138 passing tests. These are local technical observations, not production
release evidence. Report: `artifacts/test-gates/meet-companion-scoped-layers.v1.json`.

Composition of the read-only query with frozen-head and filtered-snapshot
adapters protects SRP/ISP. Existing global service/repository locators retain
DIP coupling debt; an injected scoped-query factory is a cleaner future seam.
The owned model process/toolbox, a separate Hub-reserved model run, combined
runtime acceptance and production rollout remain pending.


### Owned model execution port transport

A closed, sequenced local port channel now lets a model child request only
explicitly injected owner execution callbacks. It is disabled by default,
bounded to 32 calls per answer and unavailable for speech. Duplicate/nonfinite
JSON, unowned ports, oversized replies, wrong sequences and deadline expiry
terminate the owned child. The original Task controls remain cooperative.

The assigned HTTP adapter runs each signed Hub tool request in a separate
short-lived owned helper. Its assignment, endpoint and key come from the parent,
never from model arguments. Cancellation while HTTP is pending kills both
children; there is no orphan HTTP thread or retry after revocation. This is
a delegated execution port, not an independent Task queue or orchestration loop.

The 62-test regression includes actual subprocesses, Hub HTTP and SQL Registry
with explicit synthetic Task/MCP/policy fixtures. A separate isolated nonroot
read-only, no-network standalone Worker gate passes with 38 unique source files.
These observations do not establish model-answer grounding or production
release evidence. Report: `artifacts/test-gates/meet-companion-execution-ports.v1.json`.

The installed assigned dialog does not negotiate these ports yet. It still
requires a separate Hub-reserved model run over admitted actual model inputs
and tool sources before execution, exact result binding, and combined normal
Task acceptance. The existing selector transport retains a long lifecycle
method; an exchange-state object is the cleaner future structure. Separate
frame-codec and assigned HTTP adapters protect SRP/DIP without granting model
children Hub credentials or Task ownership.


### Exact tool inputs for a separate model run

The Hub now has a focused admission verifier for tool results consumed by a
subsequent model run. It verifies the exact completed Tool RUN assignment and
result digest, resolves admitted immutable request bytes under tenant/project
scope, and rechecks current source policy, revision, environment and chat
revision. Output SRC pins must match that tool's actual result origin, content
and policy. Being registered in the Evidence Registry alone is insufficient.
Repeated or unsuccessful runs and changed, foreign or revoked inputs fail closed.

Sixteen new cases use actual HTTP Tool admission, SQL content and Registry
stores with explicit synthetic Task/MCP/index/policy fixtures. Together with
existing scoped tools, graph/layers, Registry and owned HTTP subprocess tests,
85 tests pass. Deliberate SQL corruption fixtures and an independently
Hub-issued other-domain source exercise refusal paths. Report:
`artifacts/test-gates/meet-companion-model-inputs.v1.json`.

This verifier issues no identities and does not execute or reserve model runs.
The model controller must own current assignment checks before and after
admission, reserve a separate model run before execution, and accept only its
exact bound result. Installed dialog/toolbox wiring and combined Task runtime
acceptance remain pending. These are local technical observations, not grounded
model-answer or production release evidence.

The read-only admitted-content port separates persistence from domain admission
(SRP/DIP/ISP). Existing global policy/service locator coupling remains; injected
controller ports are the cleaner structure, with Registry ownership on the Hub.


### Separate Hub model-run reservation and result service

The Hub model lifecycle service reserves a separate RUN over the immutable
actual model request and verified tool output sources. Exact backend/model,
explicit automatic execution authorization and a declared environment digest
come from a closed Hub-owned profile. The profile is not enabled or bootstrapped
by this change. Missing authorization fails closed. Model tool messages must
match deterministic untrusted renderings of the verified Hub tool results.

Result acceptance revalidates the reserved assignment projection, immutable
input source, current chat/repository/model policy, tool source admission and
exact output bytes. OpenAI-compatible and Ollama result protocols and output
budgets are checked. Tool-call proposals are recorded as model results without
executing tools. Replay, duplicate completion and reuse of a Tool RUN are denied;
unrelated source revisions do not interrupt current chat.

Twenty-six model lifecycle tests use real SQL Registry/content stores and
explicit synthetic model-result and Task/MCP fixtures; 95 related tests pass.
No real model provider request is executed by this service gate. A Task revoked
before finish binding may leave its model run reserved, which cannot accept
output; Hub lifecycle cancellation cleanup remains required. Report:
`artifacts/test-gates/meet-companion-model-runs.v1.json`.

The signed model route/bootstrap, cooperative owned Worker transport, actual
per-provider-request reservation and completion, assigned dialog/toolbox and
combined normal Task acceptance remain pending. The environment digest is a
Hub-declared profile binding, not proof of an actual deployed image. These are
local technical observations, not grounded model-answer or release evidence.

Separate policy, input admission, persistence and Registry lifecycle ports
protect SRP/DIP. The finish controller retains intent-resolution/lifecycle
readability debt; a focused immutable intent reader is the cleaner structure.


### Signed model lifecycle HTTP transport

The Hub now exposes the separate `/internal/companion/task-model` reserve/finish
route. Closed payloads and dedicated request/response HMAC domains prevent
legacy tool/grant authority from acting as model lifecycle authority. Responses
are bound to the exact request, and completion must match the exact reserved
run and submitted result digest. Invalid query/auth/transfer/signature inputs
fail closed. The Worker inherits only its validated Task grant's endpoint,
key, original deadline and no-redirect/no-proxy opener.

`ANANTA_MEET_COMPANION_MODEL_RUNS_ENABLED` is separately default-off. Bootstrap
requires the current Hub Task-tool service; an explicit authorized Hub model
profile is still mandatory. No productive configuration was enabled.

Thirteen HTTP/bootstrap cases include actual loopback Worker HTTP and SQL
Registry reserve/finish, cancellation, unsigned replies and correctly signed
foreign result/run replies. With related model/input/tool/Registry checks,
108 tests pass. A separate standalone read-only nonroot, no-network, no-Hub
container gate verifies 41 unique current source files in the actual image.
Report: `artifacts/test-gates/meet-companion-model-http.v1.json`.

The model HTTP client is synchronous and is not yet negotiated by the installed
model runtime. A cooperative owned helper, per-provider-request reservation
and exact completion, assigned dialog/toolbox, combined normal Task acceptance
and Hub cleanup of outstanding cancelled model runs remain required. This
transport gate uses explicit synthetic model result/Task/MCP/source fixtures;
it does not execute a real model provider or establish grounded answer/release
evidence. Repeated bounded signed HTTP plumbing retains duplication debt; an
injected signed-channel adapter is the cleaner future structure (SRP/DIP).


### Cooperative owned model lifecycle HTTP helper

Model reservation and completion now have a typed cooperative owned HTTP
helper. The parent supplies the validated assignment, endpoint, key and
original deadline through its private pipe; model arguments cannot replace
transport authority. Each request owns one short-lived subprocess, bounded
to 20 seconds or the remaining original Task duration. Owner control checks
continue during HTTP, and cancellation kills the helper and discards output.
No background HTTP thread or retry is introduced.

Three new actual subprocess/loopback Hub HTTP/SQL cases cover reserve/finish,
pending-HTTP cancellation and authority replacement refusal. The related
57-test regression passes. A separate standalone read-only nonroot, no-network,
no-Hub image gate verifies 43 unique current files and constructs the adapter.
Report: `artifacts/test-gates/meet-companion-model-process.v1.json`.

These tests use explicit synthetic Task/model-result/MCP fixtures. The helper
is not yet negotiated by the installed dialog. A real provider transport
wrapper must reserve before each inference request and complete its exact
result before acceptance; assigned toolbox/dialog wiring, bounded larger model
frames, combined Task acceptance and Hub reserved-run cancellation cleanup
remain required. No grounded model-answer or production release claim is made.

Typed lifecycle and single-request runtime separation protect SRP/DIP. Tool
and model helper scaffolding retain duplication debt; a focused injected
private-request adapter can consolidate process setup without broader execution
authority or a new Task orchestration loop.


### Per-request assigned provider Hub run binding

A focused provider adapter now reserves a separate Hub model RUN before each
actual `chat` request and completes its exact result before returning it. The
model child uses its owner-authorized local model port; Hub keys remain in the
owned HTTP helper. Model listing preserves the existing provider port.

Three actual model-child/provider-HTTP/owned-helper/Hub-HTTP/SQL cases prove
two distinct pre-reserved requests and exact completions, no provider execution
without Hub authorization, and no reply or second inference after authorization
is withdrawn during the first provider request. Provider responses and Task/
MCP/source fixtures are explicitly synthetic; no productive inference, GPU or
grounded tool-based answer is established. The related 61-test regression and
a separate standalone isolated container gate with 44 unique current files
pass. Report: `artifacts/test-gates/meet-companion-assigned-provider.v1.json`.

Assigned dialog/toolbox and larger bounded tool-history frames, installed normal
Task negotiation and combined Task/browser/model-tool acceptance remain pending.
Provider exceptions can leave a reserved run until Hub-owned cancellation/
failure cleanup; the adapter cannot finish evidence on its own. Provider
composition and the chat/models protocol protect SRP/DIP while existing backend
environment coupling remains. These are local technical observations, not
production release or grounded model-answer evidence.


### Bounded larger local model request frames

The explicitly owner-injected `model` port now accepts request frames up to
128000 bytes. Other request ports and all replies remain bounded to 32768
bytes. Child and owner apply the same fixed per-port policy; arbitrary limits
are refused. Existing sequence/budget, default-off authority, speech exclusion
and cooperative deadline/cancellation behavior remain unchanged.

Actual subprocess tests accept a 40000-byte model history, reject that size
for the tool port, and reject oversized model frames before owner dispatch.
The actual model-child/provider-HTTP/Hub-helper/SQL integration also completes
two separately pre-reserved requests with a 40000-byte synthetic history. The
32-test regression and separate isolated standalone image gate with 44 unique
current files pass. Report: `artifacts/test-gates/meet-companion-model-frames.v1.json`.

These are synthetic provider/message-history observations, not an installed
tool-grounded dialog answer or production release evidence. Assigned toolbox/
dialog, installed Task negotiation, combined acceptance and reserved-run
cancellation/failure cleanup remain pending. Fixed pure frame policy protects
SRP; the selector lifecycle method retains readability debt that a focused
exchange-state object could reduce.


### Assigned eight-tool toolbox and exact model sources

The assigned toolbox offers the eight existing read-only definitions using
focused assigned execution adapters. Only the injected owner `tool` port is
called; no legacy assist/worker-key lookup is used. Exact Hub responses are
copied into bounded per-answer receipts and deterministic untrusted rendering.
Reset removes prior receipts. The assigned environment fixes the 6000-character
rendering budget so legacy truncation settings cannot change Hub model inputs.
Missing required arguments fail bounded before execution; invalid/refused tool
input cannot become unregistered model tool-role material.

An actual model-child/owned-helper/provider-HTTP/Hub-HTTP/SQL case retrieves a
Hub Task tool result and registers a separate model RUN over the actual model
request plus the Tool output SRC before provider HTTP. The 52-test regression
and isolated standalone image gate with 46 unique current files pass. All eight
definitions are offered; this gate exercises retrieve, while separate scoped
graph/layer gates verify those Hub handlers. Source/MCP/provider/Task fixtures
are explicitly synthetic, not productive inference or release evidence. Report:
`artifacts/test-gates/meet-companion-assigned-tools.v1.json`.

Assigned dialog composition, installed normal Task negotiation, combined
Task/browser/model-tool acceptance and Hub outstanding-run cleanup remain
pending. Separate tool execution and receipt state protect SRP/DIP; the small
argument conversion loop retains duplication debt that a pure public
spec-normalization function could remove.


### Persistent assigned dialog composition

The assigned dialog now composes the existing persona/router with its scoped
toolbox and per-provider-request Hub run adapter. It uses per-instance state,
not the legacy global dialog or assist transport. A narrow execution-port
binder permits only one active answer, unbinds in `finally`, and resets receipts
and model completion before the next answer. Deterministic previous-answer
explanation does not perform another model request or reuse its old run identity.

An actual model subprocess/backend/helper/provider/Hub HTTP/SQL integration
answers a repository question with an exact registered Tool source bound into
its separate model run. Provider/MCP/Task/source fixtures remain explicitly
synthetic. The 53-test regression passes; the standalone isolated image inventory
now includes 52 unique files, including the actual consumed dialog/router/trace/
model-backend modules. Report: `artifacts/test-gates/meet-companion-assigned-dialog.v1.json`.

The installed execution entry does not select this factory yet. Explicit
Hub-owned normal Task negotiation, combined Task/browser/model-tool acceptance
and reserved-run cancellation/failure cleanup remain required. The existing
trace handles repository path snippets; Registry pins remain in separate
receipts/completion and cannot be fabricated as path citations or release
evidence. Per-instance composition and answer-scoped ports protect SRP/DIP;
provider environment selection remains coupling debt best addressed by an
injected provider factory. No productive inference/GPU/release claim is made.


### Hub-owned signed model selection

When model lifecycle bootstrap is explicitly enabled, its narrow projection
policy is attached to the existing Task grant issuer. The signed grant may now
include closed `model_execution` metadata: backend, model and declared
environment digest only. Current Hub model/source authorization is checked;
withdrawal or mutation of the attached policy/profile during signing prevents
publication instead of downgrading to a legacy model. No projection policy
preserves the original response shape. Worker validation rejects endpoints,
commands and malformed profile fields.

Nine new cases include actual grant signing and Worker HTTP consumption with
explicit synthetic Task/source/model-policy fixtures; 89 related tests pass.
The isolated standalone image inventory remains 52 unique files. Report:
`artifacts/test-gates/meet-companion-model-selection.v1.json`.

The installed Worker execution entry does not consume the selection yet.
Closed parent-owned dialog negotiation, combined normal Task acceptance and
reserved-run cleanup remain pending. Metadata selects a model; it is not
execution or release evidence. A narrow projection port protects SRP/DIP/ISP;
source-resolver locator coupling remains best addressed through injection.


### Installed Worker selection of the assigned dialog

The normal Task runtime now composes its execution adapter from the previously
signature-verified Hub grant. A closed model profile requires both typed owner
tool/model ports and is carried only on answer requests. The actual default
`companion_execution_runtime` selects the assigned dialog from this parent
negotiation, overrides conflicting Worker backend defaults and refuses later
profile changes. Environment flags alone cannot activate this path. Speech
rejects model negotiation fields; missing selection preserves compatibility.
The initial pipe frame is bounded to 12000 bytes to retain the full 2000-character
Unicode message budget with profile metadata. Larger local model frames keep
their separate limits.

Actual installed-entry tests perform signed Tool/model Hub HTTP and real model
backend/provider HTTP with exact Registry source binding, using synthetic
provider/MCP/Task/source fixtures. The 53-test regression passes; the isolated
standalone image inventory contains 53 unique current files. Report:
`artifacts/test-gates/meet-companion-worker-selection.v1.json`.

Combined normal SQL Task/admission/executor/browser/model-tool acceptance,
slot release and outstanding reserved-run cleanup are still required; this
combined runtime migration is not yet proven. No productive inference/GPU/
release claim is made. Focused Task composition and shared profile validation
protect SRP/DIP/ISP. Legacy general-answer/TTS compatibility and provider
environment coupling remain; a focused provider factory/separate speech entry
are the cleaner future seams.

### Hub cleanup of unfinished assigned model runs

The opt-in model-run bootstrap installs a focused cleanup service on the existing Hub dialog Task adapter. After an authoritative terminal Task CAS, the Hub reads the stored tenant/project/runtime/lease binding, verifies reserved identities through the Evidence Registry and terminates only pending model runs whose immutable model-input content matches the reserved input digest. Cancelled Tasks produce cancelled runs; completed or failed Tasks without a model result produce failed runs. Existing or concurrently completed results remain immutable. Repeated finish callbacks retry after bounded persistence errors without undoing Task cancellation.

The local synthetic gate `artifacts/test-gates/meet-companion-run-cleanup.v1.json` records 13 cleanup tests, including three real SQL Task terminal transitions, plus separate model/HTTP and Task/media/browser regressions. It is not production release evidence. Global Task finalization outside dialog callbacks and Hub crash-without-finish reconciliation remain open, as does combined container acceptance with assigned model/tool selection.

The focused persistence port and terminal service preserve SRP/DIP/ISP. Existing dialog Task code still combines parent and child lifecycle cleanup; that preserved SRP debt can later move behind terminal observers.

### Assigned model/tool path in the integrated Worker container

The private integrated gate now adds signed assigned-model variants. They run the installed `companion_execution_runtime` and persistent assigned dialog rather than the previous synthetic model command. The synthetic provider and scoped MCP fixtures verify prior Hub model reservation, two registered input-source pins and actual source-rendered tool messages; only provider data and CPU TTS are replaced. The first assigned cancellation case passed in 22.35 seconds, including exact SQL tool/model terminal states, browser departure, process cleanup and executor slot release. Answer and normal Task-deadline scenarios are still running and are not yet acceptance evidence. See `artifacts/test-gates/meet-companion-assigned-integrated.v1.json`. This local synthetic gate does not establish production release evidence or complete runtime migration.

### Terminal model-run reconciliation without Worker callbacks

Opt-in assigned-model bootstrap now installs a bounded terminal-run reconciler in the existing Hub Meet deadline lifecycle. After each deadline tick, an independent recovery step scans SQL dialog Tasks with pending runs using a bounded keyset cursor. It uses stored terminal Task status and exact runtime/lease bindings plus Registry-verified immutable identities; live Tasks are never force-completed. A crashed Worker needs no finish callback: once the existing deadline job or another authoritative Hub Task path records a terminal state, this recovery step can settle unfinished model runs. Persistence errors and malformed contexts are counted, advance the cursor, and retry on a later scan. Deadline-step errors do not suppress cleanup; shutdown remains cooperative and error logs omit payloads.

`artifacts/test-gates/meet-companion-run-reconciliation.v1.json` records actual SQL recovery tests and the Hub lifecycle test. A real crashed-container/original-deadline recovery gate remains outstanding. These synthetic technical observations do not prove production release readiness. Focused paging and cleanup ports preserve SRP/DIP/ISP; the Hub retains all Task and recovery ownership.

### Actual assigned Worker-container failure

The new `assigned-worker-crash` scenario passed in 106.72 seconds. It admits a normal SQL Task, joins through the installed assigned Worker/browser path, executes the synthetic scoped tool and starts actual provider HTTP only after the Hub model Run is reserved. It then kills the actual Worker container, confirms Docker reports it stopped, and observes machine departure/camera removal without a reply. The Hub services are driven through their existing headless `run_once` ports against the unchanged original 90-second Task deadline. The Task becomes failed and exactly one successful Tool Run plus one failed Model Run remain, with no Worker finish callback or legacy endpoint use.

See `artifacts/test-gates/meet-companion-worker-crash.v1.json`. This is synthetic local technical evidence, not production release evidence. The existing background ownership is verified separately; a killed container cannot return an HTTP executor-slot acknowledgement. Combined answer/normal-deadline scenarios are still running.

### Explicit operator configuration for the assigned model profile

When Hub model runs are explicitly enabled, bootstrap accepts `ANANTA_MEET_COMPANION_MODEL_PROFILE` as bounded strict JSON. Its only fields are `backend` (`openai` or `ollama`), `model` (the approved provider model name), `environment_digest` (64 lowercase hexadecimal characters from the operator-approved execution profile), and `auto_execute` (an explicit boolean). An application-supplied `MEET_COMPANION_ASSIGNED_MODEL_PROFILE` takes precedence. Missing configuration never borrows Worker model defaults or creates approval; `auto_execute=false` stays denied by the existing Hub policy. Duplicate keys, nonfinite values, excessive input and transport/endpoint fields fail with a fixed coded configuration error.

`.env.example` documents the operator variables with all execution flags disabled and an empty profile. No productive policy or deployment was enabled. The parser has a separate SRP responsibility from live authorization and shares the closed projection validator. The 23 profile/HTTP tests in `artifacts/test-gates/meet-companion-model-operator-config.v1.json` are local synthetic observations. The combined container run has reported a failure and remains under diagnosis; full runtime migration is not established.

### Assigned answer/normal deadline acceptance and mid-speech recheck

The mixed integrated container run ended with eight passes and one failure in 795.20 seconds. Both new assigned answer and normal-deadline cases passed: each delivered three unique replies through the installed dialog/tool/model HTTP path and persisted exactly six successful Hub tool/model Runs. Normal completion followed the original 90-second Task deadline and released the live Worker executor slot. Signed Hub selection overrode conflicting Worker backend defaults. Assigned model cancellation and actual Worker-container failure passed in separate gates.

The only failing mixed-run case was `speech-mid-speech`: its browser wait for active audio timed out in the mid-speech phase. Chat delivery precedes owned clip preparation, which has a separate 120-second bound, while the test allowed only ten seconds. The test now waits within that owned preparation budget and requires fresh PCM instead of cumulative historical activity before pausing. A targeted regression recheck is running; the original failure is not erased and full runtime migration remains unverified until that check passes. See `artifacts/test-gates/meet-companion-assigned-integrated.v1.json`.

### Hub retirement of legacy entrypoints

Assigned Hub Task-grant mode now disables `/internal/companion`, `/internal/assist/retrieve` and `/internal/assist/tool` by default, returning `meet_companion_legacy_retired_use_hub_tasks` with HTTP 410 before legacy execution. A Hub operator can explicitly select `ANANTA_MEET_COMPANION_LEGACY_ENABLED=1` for compatibility during gradual rollout, or `=0` to retire them independently of mode. Legacy-only deployments preserve compatibility without an override. Assigned endpoints remain unchanged; invalid policy is a bounded denial. The legacy Worker maps Hub 410 to a typed retirement and ends its supervisor without retrying. The supervisor remains compatibility debt; assigned Tasks never use it.

The retirement gate records 91 Hub tests, 24 Worker tests, a current packaged source-hash/CPU check and actual assigned cancellation against the current image. Fresh-PCM mid-speech rechecks passed for speech (73.76 seconds) and avatar (84.01 seconds). The first test correction was itself rejected by the shared helper 30-second cap; the final helper respects four bounded 30-second observations inside the existing owned preparation budget. Neither historical failure is erased. The initial timeout cause is not fully classified from the sparse original diagnostics.

See `artifacts/test-gates/meet-companion-legacy-retirement.v1.json`. Full selected model/tool mode pre/mid-audible speech/avatar control acceptance and productive provider/GPU/lipsync/rollout remain open; runtime migration remains false at that full scope. SRP is protected by a separate Hub compatibility policy; no Worker-to-Worker orchestration or new scheduler is introduced.

### Separate assigned TTS lifetime

The four selected-model source-control cases passed on the preceding Worker image in 301.27 seconds. The subsequent ownership audit found that an optional source pause during blocking TTS execution could close the shared persistent model process. Selected model mode now creates a separate owned, portless TTS helper per utterance, while model/tool execution and dialog state remain in the persistent model process. Source interruption retires only the current TTS helper; a global loss closes both, and the closed owner rejects all later answer/speech attempts before spawning. Every temporary helper is closed after output or failure.

The 22 process/execution/dialog tests exercise actual process interruption, prove that the same model process answers afterward, and prove global no-restart behavior. The final source-hash/private CPU image gate passed with 54 unique files. The separated-TTS source-control matrix and a final-image SQL/browser test that pauses an actually blocked TTS preparation are running. See `artifacts/test-gates/meet-companion-isolated-tts.v1.json`; these synthetic local observations are not production evidence.

Per-utterance TTS ownership protects SRP. The combined public typed model/TTS facade remains SRP/ISP compatibility debt; separate model-dialog and speech-preparation interfaces are the cleaner eventual split. Unselected legacy compatibility retains its existing shared process behavior. Hub Task ownership and Registry issuance are unchanged.

The final-image `assigned-speech-preparation-paused` container scenario passed in 76.48 seconds: an actually blocked TTS helper is paused, three distinct source-bound replies still arrive, all six Hub tool/model Runs succeed, and the normal live Worker slot is released. The other separated-TTS source-control matrix has reported one failure and is still completing its final case; no all-green or full migration claim is made.

The separated-TTS source-control run ended with three passes and one failure in 296.40 seconds on the pre-close-fence image. The failing `assigned-avatar-mid-speech` case recorded one retired utterance and one publication failure; the underlying exception was not captured by the original fixture. A final-image targeted rerun now records only a bounded `meet_*` error code or exception class, never private exception payloads. The final-image blocked-TTS preparation case remains positive; full source-control acceptance stays open.

### Immutable playback observations after scheduler correction

The targeted avatar-mid diagnostic rerun passed in 87.90 seconds without reproducing the earlier sparse failure. An independent deterministic test then reproduced `meet_avatar_audio_schedule_invalid`: after paired publication began, a later audio-only stall changed the scheduling anchor and the report reused that new anchor to assess earlier recorded starts. Publication now retains the actual first-PCM playback origin as an immutable measurement anchor while allowing later scheduling corrections separately. The retired-video case passes; the active-video case still rejects a real later timing violation. No synchronization threshold is relaxed and no retired-source observation is invented.

The gate records 43 related media/execution tests, the final two original-origin cases, and a current source-hash/private CPU image check with 54 unique files. Five actual selected-mode source-control/preparation-pause scenarios on the current image are running. The deterministic bug is real, but the original container exception was not captured, so attribution of that earlier failure remains unproven. See `artifacts/test-gates/meet-companion-playback-origin.v1.json`.

Measured observations and scheduling state now have separate responsibilities (SRP). The publication method still combines scheduling and report assembly; a dedicated observation accumulator remains the cleaner future extraction. Hub/Worker ownership and the strict gate remain unchanged.

### Compose transport of explicit assigned-mode configuration

`docker-compose.meet-media-hub.yml` now forwards the three assigned-mode flags and `ANANTA_MEET_COMPANION_MODEL_PROFILE` into the Hub. `ANANTA_MEET_COMPANION_LEGACY_ENABLED` uses absent-value forwarding so Compose does not manufacture a compatibility override. The media Worker overlay forwards `MEET_LLM_OPENAI_BASE_URL` and `MEET_LLM_OPENAI_API_KEY` as execution transport settings, without receiving Hub model approval/profile configuration. The Hub overlay does not receive that Worker provider key. Fixed-endpoint egress still requires the operator-approved provider endpoint in its policy; this change does not authorize network access by itself.

The 30 Compose/profile/retirement checks render with a synthetic base, isolated environment and explicit `/dev/null` env file. No productive `.env` was read and no deploy or container restart occurred. Existing default-disabled flags, networks, mounts and egress semantics remain preserved. `artifacts/test-gates/meet-companion-compose-configuration.v1.json` records this local technical observation. Policy configuration remains Hub-owned and execution transport Worker-owned (SRP/DIP).

### Current packaged selected source-control acceptance

The exact playback-origin image `sha256:2f278c0c373c04bede75c392ec2fdb33f05ff93a6fe4f6b6f6303e19dd354a59` passed all five selected-mode source-control cases in 375.62 seconds: avatar/speech withdrawal before publication, both withdrawals during publication, and speech withdrawal during blocked TTS preparation. This supersedes the earlier pending source-control matrix. The unchanged strict synchronization checks passed; isolated TTS cancellation retained the model dialog. The other eleven integrated scenarios are running separately against this same image. Local headless synthetic observations remain distinct from production release evidence.

The current host exposes an RTX 5060 Ti through WSL `/dev/dxg`, but the existing isolated native-Linux GPU fixtures require `/dev/nvidia*` and allowlisted `/host-nvidia` driver mounts, neither available on this host/service. Models are present. This is a concrete fixture compatibility gap, not proof that GPU execution is unavailable. No serving container, driver, daemon or production environment was changed.

### Isolated WSL GPU adapter and regression

The standalone Companion regression passed 584 checks with 35 explicit opt-in skips in 56.19 seconds. A separate closed WSL driver adapter now allows isolated packaged probes using `/dev/dxg` and nine fixed read-only CUDA/NVENC/D3D12 libraries. The native fixture remains the default. Isolation/native compatibility passed 44 checks in 2.90 seconds. No serving state, Worker key, service environment, network, source override or Docker socket is shared. The explicit gate is `MEET_WSL_PACKAGED_GPU_GATE=1` plus `MEET_COMPANION_PACKAGED_IMAGE=<immutable image>` with `tests/meet/test_meet_gpu_wsl_fixture.py`.

Three terminal owned speech-probe attempts (the final attempt adding the required D3D12 libraries) failed CUDA initialization with provider diagnostic CUDA failure 35 and closed code `meet_piper_cuda_fallback_forbidden`; the CPU fallback was refused. Video was not executed after speech failure. The host reports RTX 5060 Ti, 16311 MiB, driver 610.88. The diagnostic requires investigation; it does not establish that a host driver upgrade is necessary. All probe containers were removed. See `artifacts/test-gates/meet-companion-wsl-gpu.v1.json`.

### All current integrated runtime cases accepted

The other eleven integrated cases passed in 767.29 seconds. Together with the five current selected source-control cases, all sixteen scenarios passed in two complementary batches against the same immutable playback-origin image. Normal completion, cancellation, source retirement, owned process cleanup, slot release and actual Worker-crash/Hub reconciliation are verified. See `artifacts/test-gates/meet-companion-task-runtime-acceptance.v1.json`. This closes the local installed Task-runtime acceptance gap, while synthetic provider/MCP data and CPU TTS do not prove productive GPU/provider/lipsync delivery or deployment. The broader migration flag remains false at that productive scope; local runtime migration is separately recorded as verified.

### WSL Driver Store initialization correction

The explicit WSL adapter now requires one validated Driver Store through `MEET_TEST_WSL_DRIVER_STORE`, sharing only nine fixed WSL libraries and ten fixed Driver Store files read-only at their original paths. It cannot mix revisions or accept arbitrary directories. NVIDIA lists the CUDA loader, `libnvdxgdmal.so.1` and `nvcubins.bin` among its WSL requirements ([official implementation](https://github.com/NVIDIA/nvidia-container-toolkit/blob/main/pkg/nvcdi/driver-wsl.go)); the corresponding finite projection fixes observed CUDA initialization. Original native fixture defaults and security limits remain unchanged.

The exact packaged WSL speech and clip-video gates then both failed with owned timeout exit 124 after 45 seconds (two failed cases in 95.23 seconds). ONNX initialized its CUDA session; no successful media output or GPU timing acceptance is established. Host CUDA independently initialized and enumerated one GPU. Earlier initialization/code-35/native-signal diagnostics are historical observations superseded by the current startup result, not causes asserted for the remaining timeout. No driver/daemon change or CPU fallback was used. See the updated WSL report.

The final WSL/native isolation recheck passed 57 tests with two opt-in GPU skips in 2.91 seconds; the separate enabled GPU batch above ran both skipped paths and failed them. Invalid Driver Store selection fails before Docker access, and native mode refuses a WSL store rather than silently ignoring it. A separate owned 180-second cold/warm diagnostic with a private 128-MiB CUDA cache is running to distinguish startup cost from a persistent inference failure. Its budget does not change any productive Task, media or 45-second acceptance limit.

The bounded cold/warm diagnostic completed with exit zero: CUDA session setup 1.33 seconds, first actual PCM after 43.97 seconds (23808 samples), and second inference in the same loaded session after 0.0678 seconds (22272 samples). This proves GPU inference functions, while exposing a cold-start cost above the current Task preparation budget. It does not prove a new TTS process can reuse the cache. A separate bounded fresh-process speech/video diagnostic is running against the same private cache and exact image; productive limits remain unchanged.

### Prepared GPU cache works across fresh owned processes

The fresh-process diagnostic completed with exit zero. Its explicit private cold preparation took 1.24 seconds for CUDA setup plus 41.07 seconds to first PCM. A new `speech_smoke` process then passed in 3.28 seconds, including actual Piper CUDA output, 22050-Hz framing and bounded local cancellation. Another new `persona_visual_smoke` process passed in 3.69 seconds, verifying moving clip video through NVENC, 43 decoded video frames, 77 audio frames and 7460-microsecond end skew. The original media probe limits were unchanged. The private cache and entire owned container were removed afterwards.

This closes isolated prepared fresh-process GPU speech/clip functionality on the current WSL host. It does not implement productive warm readiness: cold inference still exceeds the selected Task preparation budget. The next required integration is Hub-controlled readiness/preparation before assigned GPU dispatch, followed by the actual selected runtime/provider/media chain. A Worker must not introduce an independent warmup orchestration loop. Production admission and deployment remain open.

### Task-owned CUDA cache and corrected budget audit

Source inspection corrects the previous preparation assumption: `CompanionExecutionProcess` uses an existing 120-second bound; the 20-second limit belongs to the separate model HTTP helper. The 41-second CUDA cold inference can fit existing Task/TTS controls. A new Hub warmup Task is therefore not required by this evidence and was not introduced. No Task deadline or security limit was raised.

The assigned execution factory now owns a private 0700 temporary CUDA cache with a 32-MiB limit. Fresh TTS children share it only within that Task. Its lifecycle wrapper stops execution before removal, retains scratch after optional source retirement, rejects calls after close, and cleans up on construction/close failures. External cache settings are not reused or deleted. This protects SRP/DIP through composition; the combined model/TTS facade remains documented SRP/ISP debt. Worker execution and Hub queue/Registry responsibilities remain unchanged.

The current cache image contains 55 checked standalone sources; package acceptance passed in 4.40 seconds. Cache/execution/installed-provider regression passed 22 cases in 6.64 seconds, and cache/GPU resource/driver checks passed 36 with two explicit hardware opt-in skips in 2.75 seconds. The first actual GPU Task attempt inherited the CPU test container 1-GiB limit and timed out at the browser observation (169.24 seconds); no conclusive OOM cause is claimed because that attempt lacked memory diagnostics. The corrected fixture uses the existing 4-GiB GPU/production limit. The subsequent actual cold GPU TTS and blocked-TTS source-retirement results for that cache image are recorded below. See `artifacts/test-gates/meet-companion-task-cuda-cache.v1.json`.

### Actual assigned GPU TTS and cache cleanup accepted

The actual assigned GPU Task/browser chain passed in 197.99 seconds on the cache image. It uses native Piper CUDA output without the tone seam or CPU fallback, actual installed assigned model/tool HTTP with the synthetic provider/MCP data, signed current Hub controls and encrypted browser publication. Three TTS children produced 45056, 46336 and 45312 samples in 44.50, 33.65 and 34.87 seconds. They shared one private Task cache, which was removed after Task cancellation alongside process stop and live slot release. Strict synchronization thresholds are unchanged. Current-image blocked-TTS/source retirement also passed in 76.89 seconds.

The corrected 4-GiB attempt initially delivered all three chat replies but exceeded the CPU-oriented 30-second wait for final speech completion (189.82 seconds). The explicit GPU fixture now observes final TTS up to its existing 120-second execution limit; neither Task, process nor A/V gate thresholds were raised. The JUnit invocation emitted one xunit2/record_property compatibility warning; its actual GPU properties were present and parsed. Subsequent XML invocations can use `-o junit_family=legacy`.

This verifies native GPU TTS in the installed selected Task path, not actual LLM provider/lipsync deployment. Latency remains substantial despite the bounded 32-MiB cache and needs measurement/tuning. At that acceptance stage, temporary WAV/media directories still relied on killed child finalizers; the parent-owned Task scratch implementation below addresses that lifecycle/SRP/security debt. No production release or deployment claim is made.

### Parent-owned Task scratch and forced process death

Model/TTS and media helpers now use private Task-owned TMPDIRs. The existing Worker Executor owns an outer scratch tree outside the killable Task runtime and removes it before slot release. It also preserves the progress-channel environment rather than replacing it. The old `TaskCudaCache` import remains an additive alias of the focused scratch owner. Actual interrupted children leave their synthetic WAV inside this tree; normal Task close removes it. Forced Task-runtime stop also removes the outer tree, and cleanup does not follow a symlink into another Task or directory. Operator persistent logs and external provider stores remain outside this scratch guarantee.

A separate single-threaded helper bootstrap observes its parent process through a pidfd and protects its exec child with kernel parent-death termination. This avoids thread-lifetime coupling in the calling service and covers isolated helper process groups after Task-runtime death. It supervises only execution resources; it has no Task/Hub APIs or delegation decisions. The seccomp addition permits only `pidfd_open` with zero flags. An actual constrained-container check confirms nonzero flags and pidfd signalling remain denied, while the helper runs under the existing readonly/nonroot/cap-drop/no-new-privileges policy. No syscall policy was disabled and no weaker fallback was added.

The final immutable image verifies 57 standalone sources (packaged gate 4.49 seconds). Parent-death/thread-lifetime/forced-cleanup checks passed nine cases in 3.14 seconds. The broader run passed 624 and skipped 35, initially failing two channel substitutes without an env dictionary; the compatibility fix passed 31 related cases in 7.36 seconds, including both failures. Installed model cancellation and TTS-source retirement passed two current-image cases in 96.76 seconds; the latter verifies the interrupted WAV and all scratch roots are gone. The actual narrow-seccomp container gate passed in 2.98 seconds. See `artifacts/test-gates/meet-companion-owned-task-scratch.v1.json`.

SRP is protected by separate scratch ownership and process-lifetime infrastructure; the process-wide guard preserves LSP for callers whose spawning thread exits. Hub Task/Registry ownership is unchanged. The combined model/TTS facade and large runtime composition remain SRP/ISP extraction debt. These synthetic technical observations do not complete productive LLM/GPU/lipsync deployment or release admission.

The same final 57-source image also passed the actual native GPU TTS installed Task/browser gate in 202.12 seconds. Three utterances produced 42496, 46336 and 41216 samples in 44.37, 34.02 and 35.07 seconds. They shared one private Task cache, and all scratch roots were absent after cancellation and slot release. CPU fallback remained disabled and synchronization/execution/Task limits remained unchanged. The initial invocation failed before container startup because its isolated Node executable was missing from PATH; the corrected fixture invocation passed. The LLM/MCP remain synthetic in this gate, so actual LLM plus configured avatar/lipsync and productive release admission remain open. Cache latency still needs measurement and tuning; these timings do not prove a cache-eviction cause.

### Actual pinned GPU LLM provider and prior Hub runs

The former test model volume is no longer present. A separate restoration helper fetched the pinned public Qwen 2.5 1.5B manifest and all five blobs into a fresh isolated test directory, checking exact SHA-256 digests and sizes. Existing directories are never overwritten; bad downloads remove only their newly owned directory. An additive WSL provider fixture runs the pinned Ollama image with readonly model and closed driver bindings on an internal network, nonroot/cap-drop/no-new-privileges, no cloud or public ports, and a bounded 360-second outer lifetime. No serving volume, service or credentials are used.

The actual GPU provider/assigned-process/Hub model HTTP gate passed in 48.08 seconds. Each of two real model requests required a distinct prior Hub SQL Registry reservation, and each exact raw result completed its reservation successfully. The pinned model reported 1105419304 bytes of GPU residency; CPU-only residency cannot pass. Both runs and sources remain explicitly test/synthetic and cannot promote to production release evidence. Fixture/related regression passed 87 cases with two explicit hardware skips in 6.69 seconds. See `artifacts/test-gates/meet-companion-real-llm-provider.v1.json`.

Immutable pins, verified model restoration and bounded provider execution are separate SRP responsibilities. Extracting the small shared pin module removes the restoration helper dependency on the old combined provider/Worker fixture; existing constant imports remain compatible. That old combined fixture and the production model/TTS facade retain documented SRP/ISP extraction debt. Full installed Task/browser operation with this actual LLM plus configured avatar/lipsync, GPU cache latency tuning and productive rollout/release admission remain open.

### Installed actual LLM, native GPU speech, and remaining clock boundary

The installed SQL Task/Hub/Worker/executor/live-browser path passed with actual pinned GPU Ollama inference and CPU tone in 76.98 seconds. Three model requests followed three scoped tool executions; all six prior Hub-issued test runs succeeded. Browser reply observations now use membership-classified machine messages and actual unique-text counts, retaining exact reply count without requiring invented synthetic prose. Fixture/provider regression passed 28 cases in 6.60 seconds and selection/model-HTTP/tool checks passed 39 in 6.41 seconds.

Combining real GPU TTS exposed real timing errors; those failed results remain recorded rather than being reclassified as success. A deterministic 100-ms initial buffer-status wait reproduced first-segment drift. Moving queue observation before opening video fixed it: actual first-video deltas were -3/-2 microseconds. Later clip calls then revealed 59–71 ms delay and negative audio lead. A bounded prior-swap-latency prediction, 120-ms PCM lead within the existing 200-ms queue, and refreshed timestamps after blocking clip calls improve scheduling without changing the half-frame gate or immutable playback origin. The 75-ms predictable-swap reproducer failed before this correction; unexpected later stalls still fail the same strict gate.

The more important SRP/DIP improvement separates browser status/chat/pulse polling into `CompanionBrowserProgress`, on a 100-ms owner-thread cadence. Every operation still checks current Hub controls/freshness and source revisions; native publication also retains its own authority fence. Tests prove cached progress cannot extend closed Hub authority or delay avatar retirement. Browser-progression/source/runtime regression passed 59 cases in 8.16 seconds, with 22 authority/cadence checks in 3.20 seconds.

Regular native lease renewal during an open speech graph changes the graph authority. The Worker now defers that resource renewal until native speech is no longer starting/open, preserving its existing expiry and generation fence. A deferred attempt never updates the successful-renewal time; a one-second retry cadence bounds its IPC work. Hub denial is still checked first and unknown native states fail closed. Deferred-renewal/source/timing regression passed 65 cases in 8.46 seconds. The current immutable image contains 58 checked sources and passed package acceptance in 4.31 seconds; Worker boundaries pass for 177 files.

The latest combined actual GPU gate still failed in 241.61 seconds: all three native TTS outputs completed (301312/476672/282880 samples; 44.07/33.80/35.01 seconds), two actual multisegment publications passed with a maximum completed drift of 29446 microseconds, and 18 renewal deferrals preserved the success clock. The last multisegment publication still failed `meet_avatar_audio_schedule_invalid`. The host-side loop and last-operation latency prediction cannot guarantee strict clock alignment under variable browser/Hub IPC. The required next seam is a narrow native prepared-sequence publication port driven by the existing speech sample clock, preserving old APIs and finite source/queue/deadline bounds. No safety threshold, Task deadline or TTS execution limit was raised. The explicit combined fixture only extends observation to the original 300-second Task window (receive 300, watchdog 340, pytest 360).

See `artifacts/test-gates/meet-companion-installed-real-llm.v1.json` for each failed image/result and numeric diagnostics. This is partial delivery acceptance, not a production release. The documented MuseTalk endpoint did not answer its three-second health probe; portrait and idle assets are present. Configured lipsync acceptance, GPU startup latency work and productive rollout/release admission remain open. The large runtime and combined model/TTS facade retain SRP/ISP debt; the new browser-progress seam does not create a Worker task queue or change Hub orchestration/Registry ownership.

### Assigned native sample-clock Worker integration

The assigned Worker now supports the additive native sequence port under the explicit default-off `MEET_COMPANION_NATIVE_SEQUENCE=1` flag. It renders bounded clips before opening speech, keeps current Hub/source checkpoints running during pollable preparation, binds only the actual returned speech generation and validates per-segment played-sample quality receipts. Selected native failures never silently fall back to the old host-timed path. Explicit avatar withdrawal may still retire optional avatar output while bounded authorized audio continues. Active sequences defer lease replacement without extending the original Task or source deadline.

Native quality requires actual renderer observations for every sample window and exact completed speech, with the unchanged41667us bound. One-frame final tails are balanced into two valid clips without exceeding120 frames per clip. Separate browser adaptation and sequence publication modules protect SRP and narrow injected ports protect DIP; the large existing runtime composition remains documented debt. No Hub task, policy or Registry ownership moves into the Worker.

65 targeted Worker/source/timeline tests pass in8.38seconds;61 frontend sequence tests pass in602ms and Angular application TypeScript passes. The60-source packaged Worker passes its package gate in4.26seconds. The first installed native CPU-tone gate failed at room join after61.01seconds, before native decoding/publication. This is partial implementation with an open installed acceptance gate, not productive migration or GPU/lipsync release evidence.

A bounded diagnostic repeat localized the initial native join failure to empty sequence cleanup returning false, which the existing page lifecycle correctly treated as an unconfirmed stop. Unscoped cleanup now confirms that no resource remains; unknown targeted handles still fail. The page-admission/controller lifecycle regression passes28cases in723ms. Actual installed publication acceptance is still pending.

After that correction, the installed native CPU-tone gate passes in83.13seconds: three native decoded publications complete, six Hub test runs succeed and no Legacy callback is used. Source cancellation terminates the assigned session cleanly. This establishes actual installed browser decoding/publication for this private Chromium fixture, with synthetic model/tool inputs and CPU tone. It does not establish actual GPU, broader withdrawal cases, portable browser support or productive lipsync/release admission.

The combined installed native sequence gate now passes in236.00seconds with the pinned actual GPU LLM and native GPU TTS (CPU fallback disabled). Three complete utterances contain241664/124672/394240 samples, including two multisegment publications. Maximum native renderer start-sample drift is20000us under the unchanged41667us bound. All six Hub-issued test model/tool runs succeed; Legacy calls remain zero, private Task cache is shared and removed after cancellation. All1782frontend tests also pass in14.41seconds. Native source-withdrawal matrix and block verification remain pending. Scope remains synthetic project/repository/tool data plus the local avatar renderer: configured MuseTalk, productive release and portable browser support are not established. Earlier host-timed failures remain historical observations, superseded only for this explicitly selected native path.

Nine installed native source-withdrawal cases pass in695.18seconds with actual SQL Hub/Worker/Chromium execution and synthetic model/CPU-tone inputs, including four-second active speech and killed speech preparation. All use zero Legacy callbacks. A separate explicit test-only `MEET_COMPANION_INTEGRATED_GPU_WITHDRAWAL=1` additionally selects assigned speech-mid-speech for the actual model/GPU fixture. That gate must observe exactly one completed native GPU utterance longer than10seconds and an unchanged early retirement boundary before half its PCM is submitted; any later second utterance or short substitute fails. Normal actual GPU acceptance still requires all three complete utterances.21 fixture validation tests pass in3.68seconds; the new actual hardware withdrawal result remains pending.

The remaining five normal/cancellation/deadline cases passed; the first crash result was spoiled only by optional diagnostics attempting exec in the intentionally stopped container. Diagnostics now preserve Hub SQL results and explicitly report unavailable browser observation; two regression tests pass. The isolated actual crash/deadline/reconciliation case passes106.49seconds. All16current-image native lifecycle cases are individually verified across batches.

The first actual GPU withdrawal attempt failed197.73seconds at model inference before any TTS, during the broader native suite; contention is not proven as its cause. After the broad suite ended and a coded early model-error diagnostic was added, the isolated hardware gate passes123.98seconds. Actual native TTS produced260608samples (>10s) in44.54seconds. Current Hub speech withdrawal stopped PCM before half was submitted; exactly one utterance was produced, all subsequent model replies did not resume speech, all six Hub test runs succeed and the private cache is removed. This verifies actual long GPU withdrawal while preserving all original limits.

Full repository prepare passes1782frontend/build/static/leakage/Go unit-vet checks. The Node suite finishes1796pass/1fail/5skip in877.816seconds; a separate native two-source audio-broadcast test fails after screen-audio revoke waiting15seconds for renewed output-ready. Infrastructure was not reached. Therefore full block completion remains open despite the verified native Companion path. Missing MuseTalk and productive release/latency work remain explicit.

The third full Node check reports1800pass/1fail/5skip in849.756seconds. Passive projections keep source authority/generation/publication stable; a separate native incoming microphone clock closes with expiry code3 and the output correctly becomes silence. This does not invalidate the verified Companion own-speech sample-clock path, but keeps the required repository block gate open. A private test-only source_clock.go overlay now captures pre-close accepted/offered SenderReport counts and report-versus-quarantine expiry booleans in a closed v2 snapshot (v1 remains accepted unchanged). It samples no new time or authority, exports no IDs/NTP/RTP/SSRC/content and keeps the12second clock policy unchanged.9unit tests pass82.698ms; five paired actual cases pass210.746seconds, with no expiry subtype observed in that successful run. No release source or production policy changed.

Final complete `npm run check` exits0:1782frontend tests,1803Node passes/0fail/5explicit skips in847.965seconds; build/static/leakage/Go unit-vet pass.14optional live-infrastructure gates remain explicitly skipped, not accepted. The closed native sequence track is now locally complete with all16installed lifecycle cases and actual GPU multisegment/withdrawal acceptance. Earlier unrelated incoming-clock failures remain retained and unexplained, not claimed fixed. Parent Meet scope still requires configured MuseTalk, latency tuning and productive rollout/release admission; default-off opt-ins and original limits remain unchanged.
