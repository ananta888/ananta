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
  sh -c "python -m pytest -q tests/test_meet_snake_avatar_sync.py tests/test_meet_companion_dialog.py tests/test_meet_assist_retrieve_route.py"
```

Die Tests laufen ohne FFmpeg (injizierter Encoder) und ohne Browser (Ports als
Fakes mit deterministischer Uhr).
