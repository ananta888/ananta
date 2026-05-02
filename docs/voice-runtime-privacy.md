# Voice Runtime Privacy Defaults

Dieses Dokument beschreibt die Privacy-Defaults fuer Voice in Ananta.

## Defaults

- `VOICE_STORE_AUDIO=false`
- `VOICE_DIRECT_CLIENT_ACCESS=false`
- `exposure_policy.voice.require_explicit_approval_for_goal=true`
- `exposure_policy.voice.emit_audit_events=true`

## Fail-Closed Verhalten

- Hub-Route `GET /v1/voice/capabilities` liefert `privacy` mit:
  - `store_audio_requested`
  - `store_audio_effective`
  - `raw_audio_persisted`
- `store_audio_effective` bleibt aktuell `false`, solange keine explizite Persistenz-Implementierung vorhanden ist.
- Audit-Events markieren `raw_audio_stored` daher immer fail-closed als `false`.

## Operator-Hinweise

- Voice-Goal-Flows brauchen explizite Freigabe (`approved=true`), wenn Policy dies verlangt.
- In `semi-public` ist Voice-Exposition standardmaessig deaktiviert.
- Fuer produktive Nutzung sollten Audio-Dateien nur kurzlebig und zweckgebunden verarbeitet werden.
