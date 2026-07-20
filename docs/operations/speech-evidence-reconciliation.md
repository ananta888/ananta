# Betrieb: Speech-Evidence-Reconciliation

## Normaler Ablauf

Hub-autorisierte Peers tauschen zuerst consentgefilterte, pair-keyed
Inventarwurzeln aus. Ein Diff enthält nur opaque Gruppen-IDs. Das bilaterale
Offer bindet Richtung, Zweck, Fields, Datenklasse, Retention, Trainerklasse,
Budget, Epoch und beide Consentversionen. Der Recipient darf nur reduzieren.

Chunks sind höchstens 64 KiB, AEAD-gebunden und als `evidence_bulk` niedriger
priorisiert als Control und Live-Transcript. ACK/Resume beginnt am ersten
fehlenden Chunk. Empfangene Daten bleiben verschlüsselt in Quarantäne; erst
lokale Admission und danach genau ein Hub-Curation-Task dürfen eine neue
Datasetversion über den kanonischen Speech-Dataset-Port erzeugen.

## Konflikte

Der kanonische Fusion-Alignment-Port erzeugt exact, punctuation, lexical,
timing, insert, delete, uncertain, speaker-overlap und incompatible-source.
Kandidaten, Revisionen und Lineage bleiben erhalten. Korrelierte Modelle bilden
keine Mehrheit; Uneindeutiges bleibt unresolved/quarantined. Eine lokale
Anzeigeauswahl ändert niemals Evidence, Receipt oder Dataset.

## Recovery

- Disconnect: Bulk pausieren, Control/Revoke priorisieren, Angebot/Consent/Epoch
  neu prüfen und am ersten fehlenden ACK fortsetzen.
- Duplicate: Digest-/Admission-/Receipt-Idempotenz liefert dieselbe Referenz.
- Crash vor Admission: verschlüsselte Quarantäne erneut prüfen; kein Plaintext
  persistieren.
- Crash nach Curation: Admission-Digest findet denselben Hub-Task und dieselbe
  Datasetversion.
- Consent-/Epochwechsel: Cursor/Root/Offer/Chunk sofort invalidieren.

## Produktiver Offline-Reconciliation-Pfad

Der Hub-Queue-Pump claimt ausschließlich persistierte `queued` Jobs und
materialisiert genau einen Child-Task. Vor der Delegation löst der Hub das
tenant-/ownergebundene Datasetmanifest auf. Für jeden deduplizierten
`source_digest` muss genau eine noch aktive, lokal admitted Audio-Evidence mit
passendem Consent- und Revocation-Epoch existieren. Fehlende oder mehrdeutige
Treffer schließen den Lauf; es gibt keinen Fallback auf lokale Pfade oder
ungeprüfte Uploads.

Der Hub entschlüsselt diese Evidence innerhalb seiner Trust Boundary,
normalisiert sie deterministisch auf PCM-S16LE, mono, 16 kHz, bündelt sie als
WAV und versiegelt das Bundle mit dem gemeinsamen Epoch-Keyring. Die
AES-GCM-AAD bindet Job, Attempt, Fence, Consent, Manifest, Policy, Ledger und
Key-Epoch. Hub und Worker mounten denselben Keyring als read-only Compose-
Secret; das Modellverzeichnis wird ebenfalls read-only gemountet. Der Hub
erzeugt den Passplan ausschließlich aus exakt gepinnten Modell-IDs und
Revisionen des verifizierten Modellkatalogs.

Der DB-basierte Collector pollt laufende Attempts. Nur der Hub verlängert
Leases, speichert Checkpoints und nimmt terminale Resultate an. Vor der
Datasetpublikation werden Fence, Deadline, Consent/Revocation und leeres
Ledger-Reserve erneut geprüft. Das neue content-addressed Manifest wird unter
dem Consent-Write-Fence atomar gespeichert; Training bleibt eine spätere,
separate Hub-Delegation. Beim Kill-Switch oder Hub-Shutdown wird der Worker
best-effort gecancelt und die DB-Autorität unabhängig von der HTTP-Antwort
gefencet.

Erforderliche Betriebswerte sind
`ANANTA_SPEECH_RECONCILIATION_INTERNAL_TOKEN`,
`ANANTA_SPEECH_RECONCILIATION_KEYRING_FILE`,
`ANANTA_SPEECH_RECONCILIATION_MODEL_DIR` sowie die expliziten Modell- und
Varianten-Allowlisten. Der Compose-Start bricht bei fehlendem Token, Keyring
oder Modellpfad fail-closed ab.

## Revoke und Remotegrenze

Lokale Jobs, Adapter und Schlüssel werden sofort gefencet. Remote wird ein
signierter, begrenzter Request mit Deadline gesendet. Nach Retrybudget bleibt
der Zustand sichtbar `unresolved`; Ananta garantiert keine Löschung auf einem
untrusted/offline Peer. Ein Teil- oder spätes Ack wird idempotent und ohne Inhalt
auditiert.

## Operatorchecks

Die produktive Hub-/Worker-Komposition wird mit
`pytest -q tests/test_speech_reconciliation_production_composition.py tests/test_speech_reconciliation_queue_pump.py tests/test_speech_reconciliation_result_admission.py tests/test_speech_reconciliation_worker_transport.py tests/test_speech_reconciliation_recovery.py`
geprüft. Die Compose-Struktur muss zusätzlich für genau eines der Profile
`speech-reconciliation-cpu` oder `speech-reconciliation-nvidia` erfolgreich
durch `docker compose ... config -q` validiert werden.

`pytest -q tests/test_peer_speech_sync_lifecycle.py` prüft den lokalen P2P-/Relay-
Lifecycle. `python scripts/benchmark/peer_speech_evidence_sync.py` prüft Leakage,
Qualität und Live-p95. Echte Browser-/Netzevidenz kommt ausschließlich aus dem
M11-Pair-/Group-E2E-Runner und darf nicht durch diese deterministischen Tests
ersetzt werden.
