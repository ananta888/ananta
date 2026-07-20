# Semantic-Speech-Runtime

Der optionale Semantic-Speech-Pfad ist standardmäßig deaktiviert
(`ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED=false`). Der Hub bleibt Eigentümer von
Freigabe, Vertrag, Epoch, Aufgaben und Widerruf. Browser und Voice-Runtime führen
nur den jeweils bestätigten Vertrag aus; sie dürfen keine Tasks oder Peer-Rechte
erzeugen.

## Modi

- `ordinary_audio`: normales Ende-zu-Ende-verschlüsseltes Audio.
- `transcript_live`: validierte Partials werden sofort angezeigt.
- `semantic_reconstruction`: Transcript plus begrenzte, consentierte Merkmale;
  die Rekonstruktion ist nur provisorisch und verändert keine Wörter.
- `delayed_correction`: Live-Transcript plus kurzlebige Source-Korrektur nach
  jedem Segment.
- `segment_only`: keine Partials; Finalisierung und Korrektur erfolgen weiterhin
  pro Segment.
- `fallback`: sichtbarer sicherer Endzustand, wenn auch der normale Audiopfad
  nicht verfügbar ist.

Live-Anzeige, Segmentdauer und Korrektur sind unabhängige Einstellungen. Ein
Segmentabschluss versteckt deshalb kein bereits sichtbares Partial. `final`,
`corrected`, `correction_failed` und `missing_source` sind autoritative
Revisionen; ein Korrekturfehler entfernt den finalen Text nicht.

## Daten- und Kryptogrenzen

Semantic-Speech-Nachrichten werden erst nach bestätigter Peer-Key-Bindung in
einem AES-GCM-Secure-Envelope versiegelt. DataChannel und Hub-Relay sehen nur
begrenzten Chiffretext; der Relaypfad darf weder Transcript noch Features in
Tasks, Datenbank, Logs oder Metriken schreiben. Audience, Session, Epoch,
Sequence, Consentversion, Ablauf, Contract- und Source-Digest werden vor der
Domainweitergabe geprüft.

Der Source-Korrekturpuffer ist ein flüchtiger, eigener Browserpuffer. Er nutzt
einen non-extractable AES-GCM-Key und enthält höchstens fünf Segmente oder
24 MiB. Er ist weder der Langzeit-Spool noch Trainings-Evidence und besitzt
keinen Dataset-Zugriff. Confirmation, Korrekturabschluss, TTL, Quoten-Eviction,
Widerruf, Sessionende und Keyverlust löschen seinen erreichbaren Zustand.

## Qualitätssteuerung und Rückfall

Feste Schwellen überwachen Paketverlust, Queuebytes, Partialalter,
Korrekturlatenz, Source-/Featureverlust und Rekonstruktionsfehler. Normale
Moduswechsel besitzen fünf Sekunden Hysterese; Nutzer-Override und Revoke wirken
sofort. Ein Semantic-, Reconstructor-, Buffer- oder Correction-Fehler darf den
normalen verschlüsselten Audiopfad und das Live-Transcript nicht deaktivieren,
sofern deren Netzwerkpfad gesund ist.

Die provisorische generische Rekonstruktion läuft ausschließlich im Receiver.
Sie kennt keine Hub-, Peer-, Taskqueue-, Dataset-, Consent- oder Worker-API.
Supersede, Abort, Deadline und Komponentenabbau brechen die Synthese ab und
geben Audioressourcen frei. Personalisierte Adapter bleiben ein separater,
zusätzlich freizugebender Pfad.

## Betrieb und Prüfung

Der content-free Runtime-Nachweis wird so aktualisiert und anschließend geprüft:

```bash
python scripts/run_semantic_speech_runtime_gate.py --write
python scripts/run_semantic_speech_runtime_gate.py --verify
```

Das Gate simuliert acht Stunden Segmentrotation, sofortige Partials, Reconnect-
Duplikate, genau einen Korrekturversuch pro Finalsegment, Backpressure, Revoke
sowie die bekannten Fehlerklassen Stream-404, Stop-409 und Chunk-413. Das
Artefakt enthält nur Counts, Reason-Codes, Quellhashes und Pass/Fail.

Bei einem Incident wird zuerst der Hub-Kill-Switch deaktiviert, anschließend die
Epoch rotiert und der lokale Puffer widerrufen. Ein laufender normaler Call wird
dabei nicht beendet. Klartext darf nicht zur Diagnose geloggt werden; zulässig
sind ausschließlich die content-free Audit- und Gate-Read-Models.
