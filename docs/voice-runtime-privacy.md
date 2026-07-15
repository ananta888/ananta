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
  Das Feld bedeutet: kein Roh-Audio wird nach Abschluss des Requests als
  Ananta-Datensatz oder Artifact aufbewahrt. Es bedeutet nicht, dass während
  der Verarbeitung zu keinem Zeitpunkt Roh-Audio existiert.
- Multipart-Parser und einzelne ASR-Backends dürfen große Requestdaten
  vorübergehend in einer requestgebundenen Temporary-Datei verarbeiten. Das
  Voice-Restricted-Compose-Profil mountet `/tmp` deshalb auch im Hub als
  begrenztes `tmpfs`; Runtime-Worker verwenden bereits eigene begrenzte
  `tmpfs`-Mounts. Die Temporary-Dateien werden beim Request-/Backend-Abschluss
  geschlossen und sind keine Voice-Ledger- oder Artifact-Retention.
- Der Hub-Langzeitmodus persistiert nur Run-/Segment-Metadaten, Task- und
  verschlüsselte Ergebnisreferenzen. Roh-Audio wird nicht in der Hub-Datenbank
  oder in Tasks abgelegt.
- Noch nicht bestätigte Langzeitsegmente werden im Browser beziehungsweise in
  der WebView als AES-GCM-Chiffretext in einem global auf fünf Segmente und
  24 MiB begrenzten IndexedDB-Puffer gehalten. Bestätigte Segmente werden
  sofort entfernt. Nach 24 Stunden sind Segmente logisch nicht mehr lesbar und
  werden bei der nächsten Pufferoperation oder Initialisierung physisch
  gelöscht.

## Operator-Hinweise

- Voice-Goal-Flows brauchen explizite Freigabe (`approved=true`), wenn Policy dies verlangt.
- In `semi-public` ist Voice-Exposition standardmaessig deaktiviert.
- Fuer produktive Nutzung sollten Audio-Dateien nur kurzlebig und zweckgebunden verarbeitet werden.
- Das Loeschen eines Voice-Profils bereinigt auch den zugeordneten lokalen
  Langzeitpuffer. Schlaegt nur diese lokale Bereinigung fehl, bleibt die
  serverseitige Loeschung wirksam und die Oberflaeche meldet den Restzustand.
