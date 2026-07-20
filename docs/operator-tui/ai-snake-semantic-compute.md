# AI-Snake: semantisches Compute erklären und konfigurieren

AI-Snake ist in diesem Bereich eine Erklär- und Vorschlagsoberfläche. Der Hub
bleibt alleiniger Eigentümer von Membership, Contract-State, Scheduler,
Task-Queue und Leases. Ein Chattext, Toolresultat, Browser oder Worker kann
diese Autorität nicht erweitern.

Die Capability-Projektion hat ein dokumentiertes Entropiebudget von höchstens
12 Bit (sechs grobe Kategorien mit höchstens vier Zuständen) und läuft nach
60 Sekunden automatisch ab. Die Pair-Ansicht trennt vier Quellen sichtbar:

- **Lokale Messung:** grobe, kurzlebige Buckets nach ausdrücklichem Opt-in.
- **Peer-Angebot:** nicht autoritative Selbstauskunft.
- **Hub-Entscheidung:** effektives Profil, Revision, Reason-Code und Ablauf.
- **Lease:** task-spezifische, ablaufende Ausführungsautorität mit Fencing.

Die Profile `off`, `conservative`, `balanced` und `custom` sowie eine
Verzögerung zwischen 2 und 20 Sekunden erzeugen nur einen Intent. Erst ein
separater authentifizierter Hub-Request mit `Idempotency-Key` und
Revision-Precondition darf den Vertrag ändern. `Widerrufen`, `Reduzieren` und
`Ordinary-Fallback` funktionieren genauso. Bis die neue Hub-Revision gelesen
wurde, zeigt die Oberfläche die Änderung als ausstehend; abgelaufene Daten
werden niemals als aktiv dargestellt.

Erklärungen verwenden nur allowlistete Zustände und stabile Reason-Codes. Sie
enthalten weder Medieninhalt noch rohe Capability-Messwerte, Signaturen oder
Secrets. Vorschläge sind immer mit `authoritative: false` und
`requires_separate_hub_mutation: true` gekennzeichnet. Strict-E2EE verhindert
Server-Compute bereits vor der Queue-Persistenz; Trusted Compute erfordert eine
separate, aktuelle Freigabe.

## Produktiver Control-Plane-Ablauf

Die Pair-Ansicht registriert zunächst den kurzlebigen öffentlichen
Ed25519-Kandidatenkey über `POST /v1/semantic-media/compute/candidate-keys` und
sendet danach das damit signierte Angebot an
`POST /v1/semantic-media/compute/capabilities`. Der private Key verlässt den
Browser nicht. Beide Requests sind an authentifizierten Principal, Session und
Epoche gebunden. Der Browser kann seine Eignung nur reduzieren; er wählt weder
Executor noch Validator oder Standby.

Nach der separaten Contract-Aktivierung darf ausschließlich der Hub über
`POST /v1/semantic-media/contracts/{contract_id}/schedule` eine deterministische
Rollenwahl durchführen und begrenzte, gefencte Leases persistieren. Die Ansicht
liest diese ausschließlich über
`GET /v1/semantic-media/contracts/{contract_id}/leases`. Contract-Revisionen,
Widerruf, Reduktion und Ordinary-Fallback entziehen vorhandenen Leases die
Autorität; abgelaufene oder veraltete Daten werden nicht als aktiv dargestellt.

AI-Snake liest eine redigierte Erklärung über
`GET /v1/semantic-media/contracts/{contract_id}/explanation`. Ein über
`POST /v1/semantic-media/contracts/{contract_id}/suggestions` validierter
Vorschlag ist reine Darstellung. Das Übernehmen erzeugt einen neuen normalen
Contract-Intent mit `If-Match` und `Idempotency-Key`; der Vorschlags-Endpunkt
selbst mutiert nichts.

## Optionaler Trusted-Compute-Worker

Server-Compute ist ein anderer, ausdrücklich freizugebender Pfad. Der Hub prüft
den aktiven Contract, dessen Digest und Revision, den separaten
Trusted-Compute-Grant, Input-Besitz, Parent-Task, Budget, Deadline und einen
registrierten Worker. Erst danach erzeugt er Lease und Child-Task. Unter
`strict_e2ee` entsteht weder Queue-Eintrag noch Worker-Task.

Der Worker-Handler `worker/semantic_media/compute_task_handler.py` führt genau
einen delegierten `semantic_compute`-Task aus. Er verwendet den Scope
`semantic.compute.execute`, ruft nur die internen Hub-Endpunkte für
Autorisierung, Input, Quarantäne-Upload und Result-Admission auf und kann keine
Peers, SFU-Komponenten oder anderen Worker koordinieren. Cancellation,
Deadline, Leaseverlust oder Fencing-Mismatch verhindern Result-Admission und
damit die Sichtbarkeit später Artefakte.

Worker verwenden die vorhandenen file-managed Workflow-Credentials
`ANANTA_WORKFLOW_HUB_URL` und `ANANTA_WORKFLOW_HUB_TOKEN_FILE`. Das zugehörige
Service-Token muss den Scope `semantic.compute.execute` und die Capability
`semantic_compute` für die erlaubten semantischen Tasktypen tragen. Das Token
darf nicht als Klartext-Environmentvariable oder Browserkonfiguration
bereitgestellt werden.

`ANANTA_SEMANTIC_COMPUTE_WORKER_ENABLED=true` registriert den Handler und nimmt
die semantischen Capabilities in die nächste Worker-Registrierung auf. Vorher
muss die Hub-Registration-Keyring-Allowlist diese Capabilities für genau diesen
Worker erlauben; andernfalls verweigert der Hub die gesamte Registrierung
fail-closed. Der Default `false` verändert bestehende Worker-Registrierungen
nicht.

## Sichere Inbetriebnahme

Peer-Scheduling bleibt ohne die Semantic-Visual- oder Semantic-Speech-Feature-
Flags deaktiviert. Zusätzlich gilt:

- `ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED=false` ist der sichere Default;
  erst nach Prüfung der Session-, Key- und Worker-Grenzen auf `true` setzen.
- `ANANTA_SEMANTIC_COMPUTE_SIGNING_KEY` muss ein eigener zufälliger Secret-Wert
  mit mindestens 32 Bytes sein; ohne ihn verweigert der Hub Contract-Signaturen.
- `ANANTA_SEMANTIC_COMPUTE_MAX_PEER_CAPACITY=1` begrenzt den konservativen
  Telemetrieadapter. Browser-Selbstauskunft kann diesen Wert nicht erhöhen.
- `ANANTA_SEMANTIC_COMPUTE_FALLBACK_HEALTHY` bildet die operatorbestätigte
  Verfügbarkeit des Ordinary-Pfads ab. Bei `false` ist Aktivierung gesperrt.

Migrationen müssen vor Aktivierung auf dem Hub ausgeführt werden. Ein Rollout
beginnt mit deaktivierten Flags, prüft Kandidaten- und Lease-Readmodels sowie
Audit-Reason-Codes und aktiviert erst danach gezielt Sessions. Das Abschalten
erfolgt über Contract-Fallback beziehungsweise Widerruf; die Hub-Task-Queue
bleibt auch beim Workerbetrieb die einzige Orchestrierungsinstanz.
