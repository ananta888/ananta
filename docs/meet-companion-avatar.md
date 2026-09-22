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

`0`, `false`, `off`, `no` deaktivieren; alles andere aktiviert.

| Variable | Default | Wirkung |
|---|---|---|
| `MEET_AVATAR_SERVICE_ENABLED` | `1` | Sprech-Clips über den lokalen MuseTalk-Lippensync-Dienst rendern (`0` = nur lokaler Renderer). |
| `MEET_AVATAR_SERVICE_URL` | `http://172.18.112.1:8189` | Basis-URL des Dienstes (`GET /health`, `POST /avatar`). |
| `MEET_AVATAR_SERVICE_PORTRAIT` | `/state/ananta-snake-portrait.png` | Referenzportrait (Asset, 512×512 PNG), das der Dienst als `image_png_b64` erhält. Fehlt es, bleibt Lippensync aus (lokaler Renderer). |
| `MEET_AVATAR_IDLE_CLIP` | `/state/ananta-snake-idle.mp4` | Vorgerenderter, nahtlos loopender Idle-Clip (3 s, 36 Frames, 256×256, 12 fps), publiziert als `persona-video-v1` mit `repeatMode="loop"`. Fehlt er, rendert der lokale `IdleClips`-Renderer. |
| `MEET_COMPANION_AVATAR_REFRESH` | `25` | Sekunden, nach denen der Idle-Clip neu geöffnet wird (Client-Aktivierung läuft nach ~30 s ab). |

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
  Test: `tests/test_meet_snake_avatar_sync.py` (3,3 s und 31 s Antworten).
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
   (`llm.answer`, Persona-Systemprompt `PERSONA_SYSTEM`).
4. `companion_explanation.AnswerTrace` protokolliert getrennt **beobachtete
   Runtime-Schritte**, **Repository-Quellen** und **den generierten Text**.
   "Wie hast du diese Antwort erzeugt?" wird daraus deterministisch beantwortet;
   fehlt Evidenz, sagt die Schlange das, statt Dateien zu nennen.

Der Worker importiert kein Hub-`agent`-Paket (`scripts/check_meet_worker_boundaries.py`).

## Tests

```bash
cd docker/compose-next
docker compose -p compose-next -f compose.tests.lmstudio.yml run --rm t-infra \
  sh -c "python -m pytest -q tests/test_meet_snake_avatar_sync.py tests/test_meet_companion_dialog.py tests/test_meet_assist_retrieve_route.py tests/test_meet_avatar_service.py"
```

`tests/test_meet_avatar_service.py` mockt den Dienst (Wire-Format, Fehlercodes,
Fallback-Policy); der markierte Integrationstest ruft den echten Dienst nur mit
`RUN_INTEGRATION_TESTS=1` und erreichbarem `/health` auf, sonst wird er übersprungen.
`tests/test_meet_avatar_assets.py` deckt Portrait-Auswahl (Env, fehlend,
ungültig) und den Idle-Clip-Pfad (MP4-Frame-Zählung, Loop-Payload, Cache,
Fallback) mit synthetischen Dateien ab; der Integrationsteil lädt die echten
Assets aus `data/meet-media/worker-state/` und ruft den Dienst mit dem Portrait
auf, sofern erreichbar.

Die Tests laufen ohne FFmpeg (injizierter Encoder) und ohne Browser (Ports als
Fakes mit deterministischer Uhr).
