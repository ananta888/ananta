# AI-Snake Lurking/Follow/Prediction

## Ziel

Die AI-Snake ist ein sichtbarer Begleiter im Operator-TUI:

1. **follow**: folgt langsam mit Abstand.
2. **lurking**: bleibt beobachtend in der Nähe statt zu kleben.
3. **prediction**: schätzt lokale User-Intention, bevor Worker/LLM angefragt werden.

## Bedienung

```bash
:ai follow
:ai lurk
:ai quiet
:ai explain
:ai off
:ai status
:ai ctx
```

` :ai explain ` setzt `point_to_target` und triggert eine erzwungene, einmalige AI-Frage (auch wenn der Modus vorher `quiet` war).

## Runtime-Verhalten

- Pro Tick werden lokale Beobachtungen gesammelt (`section`, `artifact`, `movement`, `notes_active`).
- Daraus entsteht eine deterministische Quick-Prediction.
- Ein Stability-/Debounce-Gate verhindert Worker-Spam.
- Proaktive Chat-Kommentare erscheinen nur bei hoher Confidence und Cooldown.

## Policy-Grenzen

- Notes bleiben standardmäßig **metadata-only** (`notes_active=true`), nicht als Rohinhalt.
- External-Provider-Boundary ist blockiert.
- Policy-Entscheidungen werden als `decision_ref` im Debug-State abgelegt.

## Debug-Hinweise

Im `header_logo_game.ai_snake_debug` stehen:

- `gate_reason`
- `allow_worker_request`
- `policy.worker_request`
- `policy.lmstudio_prompt`
- `allow_proactive_comment`

Damit ist transparent, warum eine Anfrage gesendet oder unterdrückt wurde.

## Training-Daten Speicherort (local-first)

Mit `:ai data path` zeigt die TUI den lokalen Pfadbaum:

- `~/.config/ananta/ai_snake/prediction_profile.active.json`
- `~/.config/ananta/ai_snake/prediction_events.jsonl`
- `~/.config/ananta/ai_snake/learned_patterns.json`
- `~/.config/ananta/ai_snake/exports/`

Weitere Trainings-Kommandos:

- `:ai data show` (human-readable Überblick)
- `:ai patterns` und `:ai pattern <id>` (Pattern-Inspektion)
- `:ai data export --stdout --format json [--include-events]` (machine-readable Bundle)
- `:ai data export <path> --format json [--include-events]` (Bundle-Datei)
- `:ai data compact` (Retention/Compaction mit Backup)
- `:ai data delete events|patterns` und `:ai data reset`
- `:ai learning on|off|pause|status` (Recorder/Profil-Steuerung)
- `:ai prediction good|bad [reason]` (Feedback für letztes Target)
