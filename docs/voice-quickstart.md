# Voice Quickstart: Vosk + wählbare lokale LLM-Korrektur

Dieser Quickstart beschreibt den gemeinsamen Voice-Pfad für Browser und die
Capacitor-Android-App. Beide Clients verwenden die Angular-Seite `/voice` und
sprechen ausschließlich mit dem Hub. Der Hub delegiert klassische
Spracherkennung an `voice-runtime` und die optionale Textkorrektur an den
isolierten `generative-corrector-worker`.

Der Corrector kann entweder ein eingebettetes, read-only gemountetes Modell
oder einen lokalen Provider (`ollama` oder `lmstudio`) verwenden. Die Auswahl
**Allgemeine LLM-Vorgabe** übernimmt aus den allgemeinen LLM-Einstellungen nur
Provider und Modell. Provider-Endpunkt und Zugangsdaten des Corrector-Workers
sind davon getrennt deploymentverwaltet und werden nicht aus `/settings` in
einen laufenden Worker synchronisiert.

Die drei Bedienmodi sind:

- **Live:** Die gewählte Audioquelle wird als 16-kHz-Mono-PCM in geordneten
  Chunks an den Hub gesendet. Vosk liefert während der Aufnahme
  Zwischentranskripte. Eine gewählte Gemma-, Phi- oder andere freigegebene
  LLM-Variante korrigiert erst das finale Transkript.
- **Langzeit bis 8 h:** Der Hub besitzt einen dauerhaften Parent-Task. Der
  Client hält genau eine Audiofreigabe offen und rotiert 60- bis
  120-sekündige Segmente. Jedes Segment wird als eigener Hub-Child-Task
  transkribiert und optional korrigiert, während das nächste bereits
  aufgenommen wird. Heartbeats, stabile Idempotency-Keys und ein begrenzter,
  verschlüsselter lokaler Puffer ermöglichen Retry und Resume.
- **Aufnehmen → transkribieren:** Die Aufnahme bleibt bis zum Absenden auf dem
  Gerät und wird anschließend als eine Batch-Anfrage über den Hub verarbeitet.

Für jede Aufnahme kann in `/voice` gerätelokal zwischen **Mikrofon** und
**Lautsprecher / Systemaudio** gewählt werden. Diese Quellenwahl ist keine
Erkennungs- oder Routing-Policy: Sie wird weder im Hub-Profil noch in einem
Session-Delta gespeichert. Der Langzeit-Run zeichnet die Quellenart lediglich
als Resume-/Audit-Metadatum in seinem Hub-Ledger auf. Beide Quellen verwenden
dieselben konfigurierten ASR-/Korrekturmodelle.

Eine LLM-Korrektur läuft niemals für jeden Live-Chunk. Sie ist ein begrenzter,
nachgelagerter Text-zu-Text-Schritt beim Finalisieren oder nach der
Batch-Transkription.

## 1. Lokale Modelle bereitstellen

Das CPU-Profil erwartet den bestehenden Voice-Modellbaum mit einem Vosk-Modell
unter `models/voice/vosk` und dem Voice-Katalog unter
`models/voice/manifests/voice-models.json`. Details zur Voice-Modellpromotion
stehen im
[Production Runbook](operations/voice-restricted-production-runbook.md#model-promotion).
Die CPU-Standardkonfiguration kombiniert Vosk mit whisper.cpp. Für eine
ressourcenschonende Installation kann derselbe Dienst explizit auf Vosk allein
begrenzt werden; dann müssen Katalog und Modellbaum keine Whisper-Artefakte
enthalten. Die dafür vorgesehenen `VOICE_CPU_*`-Variablen ändern die sicheren
Produktionsdefaults nicht.

Die Oberfläche bildet immer die vier unterstützten Adapter ab. Der Runtime-
Katalog meldet für jeden policy-erlaubten Adapter einen konkreten Status und
Grund; ein Schema-Eintrag ohne promoted Runtime-Artefakte bleibt sichtbar, aber
nicht auswählbar. Für die CPU-Varianten gelten insbesondere:

- `vosk`: benötigt `models/voice/vosk`;
- `whisper_cpp`: benötigt das ausführbare
  `models/voice/bin/whisper-cli` und `models/voice/whisper/ggml-small.bin`;
- `faster_whisper`: benötigt einen vollständigen lokalen CTranslate2-Snapshot
  unter `models/voice/faster-whisper`; das CPU-Profil verwendet standardmäßig
  `int8`;
- `voxtral`: bleibt opt-in und benötigt gleichzeitig einen kompatiblen,
  ausführbaren Runner und eine gepinnte GGUF-Datei. Die separate Android-Seite
  **Voxtral Offline** ist ein anderer, gerätelokaler Pfad und macht den Hub-
  Runtime-Adapter nicht automatisch verfügbar.

Jeder aktivierte Adapter muss vollständig im unveränderlichen Voice-Katalog
mit SHA-256-Prüfsummen erfasst sein. Danach wird er der CPU-Policy hinzugefügt,
zum Beispiel:

```bash
export VOICE_CPU_POLICY_ALLOWED_BACKENDS='vosk,whisper_cpp,faster_whisper'
export VOICE_CPU_BACKEND_FALLBACK_ORDER='vosk,whisper_cpp,faster_whisper'
```

Für den leichtesten Betrieb kann Vosk weiterhin erster und einziger aktiver
Fallback bleiben; die zusätzlich erlaubten Backends erscheinen trotzdem als
wählbare Alternativen, sobald ihre Artefakte und Abhängigkeiten bereit sind.

Der Corrector verwendet einen eigenen, read-only gemounteten Modellbaum:

```text
models/generative-corrector/
├── manifests/
│   └── model-catalog.json
└── artifacts/
    ├── gemma-2b-it/
    ├── phi-3-mini-instruct/
    └── eigenes-modell/
```

`model-catalog.json` ist eine lokale Allowlist. Ein gültiges Beispiel ist:

```json
{
  "schema_version": "ananta.generative-corrector-model-catalog.v1",
  "models": [
    {
      "id": "gemma-2b-it",
      "path": "artifacts/gemma-2b-it",
      "revision": "replace-with-immutable-gemma-revision",
      "family": "gemma"
    },
    {
      "id": "phi-3-mini-instruct",
      "path": "artifacts/phi-3-mini-instruct",
      "revision": "replace-with-immutable-phi-revision",
      "family": "phi"
    },
    {
      "id": "eigenes-modell",
      "path": "artifacts/eigenes-modell",
      "revision": "replace-with-immutable-custom-revision",
      "family": "other"
    }
  ]
}
```

Das versionierte
[Katalogbeispiel](../config/models/generative-corrector-model-catalog.example.json)
und das zugehörige
[JSON-Schema](../config/models/generative-corrector-model-catalog.schema.json)
liegen im Repository; die Deployment-Datei wird daraus unter
`manifests/model-catalog.json` angelegt.

Die Platzhalter in `revision` müssen vor dem Start durch die tatsächlich
promotete, unveränderliche Revision ersetzt werden. `id`, `revision` und
`family` dürfen nur Buchstaben, Zahlen sowie `_.:-` enthalten und müssen mit
einem Buchstaben oder einer Zahl beginnen. Jeder relative `path` muss innerhalb
des Modellroots liegen. Modellverzeichnisse dürfen weder Symlinks noch
ausführbaren Python-Code oder Pickle/PyTorch-Checkpointdateien enthalten und
müssen mindestens eine Safetensors-Datei enthalten. Modell und Tokenizer müssen
von der im Worker fest gepinnten Transformers-Version lokal unterstützt werden;
der Worker lädt keine Gewichte aus dem Netz nach.

Jede eingebettete ID der Hub-Allowlist `VOICE_GENERATIVE_CORRECTOR_MODELS` muss exakt im
Worker-Katalog vorkommen. Zusätzliche Katalogmodelle bleiben für Clients
unsichtbar; für Least Privilege sollten beide Mengen in Produktion bewusst
deckungsgleich gehalten werden. Um ein anderes Modell anzubieten, wird es daher
sowohl in den read-only Katalog als auch in diese kommagetrennte Hub-Allowlist
aufgenommen. Die Modellgewichte selbst gehören nicht in das Repository.

Alternativ erkennt der Corrector Modelle dynamisch über das im Worker-Deployment konfigurierte
Ollama-`/api/tags` beziehungsweise OpenAI-kompatible
LM-Studio-`/v1/models`. Diese externen Modell-IDs werden nicht in den
eingebetteten Katalog aufgenommen. Manuell eingegebene IDs sind nur für einen
vom Hub freigegebenen Provider zulässig; der Client kann dabei weder URL noch
API-Key setzen. Namen mit Namespace/Tag wie `Qwen/Qwen2.5-7B-Instruct` oder
`qwen2.5:7b` werden unterstützt.

## 2. Secrets und Pfade setzen

Für das produktive CPU-Profil werden die bestehenden Voice-/Restricted-Secrets
und ein zusätzliches, ausschließlich internes Corrector-Token benötigt. Jedes
Service-Token muss unabhängig und mindestens 24 Zeichen lang sein.

```bash
export VOICE_MODEL_DIR="$PWD/models/voice"
export GENERATIVE_CORRECTOR_MODEL_DIR="$PWD/models/generative-corrector"
export RESTRICTED_INFERENCE_MODEL_DIR="$PWD/models/restricted-inference"

export VOICE_INTERNAL_SERVICE_TOKEN='replace-with-a-random-voice-token'
export RESTRICTED_INFERENCE_INTERNAL_TOKEN='replace-with-another-random-token'
export VOICE_GENERATIVE_CORRECTOR_WORKER_TOKEN='replace-with-a-third-random-token'
export VOICE_GENERATIVE_CORRECTOR_MODELS='gemma-2b-it,phi-3-mini-instruct,eigenes-modell'
export VOICE_GENERATIVE_CORRECTOR_PROVIDERS='embedded,lmstudio,ollama'

# Deploymentverwaltete Provider-Endpunkte des Corrector-Workers:
export LMSTUDIO_URL='http://host.docker.internal:1234/v1'
export OLLAMA_URL='http://host.docker.internal:11434/api/generate'

export VOICE_PERSONALIZATION_ENCRYPTION_KEY="$(${PYTHON:-python3} -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Secrets gehören in den Deployment-Secret-Store und niemals in Git, Browser-
Storage, Android-Ressourcen oder URLs. Das Angular-/Android-Login verwendet ein
normales Hub-Benutzertoken; es erhält keines der internen Runtime-Tokens.

Compose übergibt `LMSTUDIO_URL` und das optionale `LMSTUDIO_API_KEY` als
`GENERATIVE_CORRECTOR_LMSTUDIO_URL` beziehungsweise
`GENERATIVE_CORRECTOR_LMSTUDIO_API_KEY` an den Worker. Entsprechend werden
`OLLAMA_URL` und das optionale `OLLAMA_API_KEY` auf
`GENERATIVE_CORRECTOR_OLLAMA_URL` beziehungsweise
`GENERATIVE_CORRECTOR_OLLAMA_API_KEY` abgebildet. Zugangsdaten nur im
Deployment-Secret-Store setzen; sie erscheinen weder in der Voice-Auswahl noch
im Capability-Dokument.

Eine Änderung dieser vier Deployment-Variablen wird erst mit einem neu
erstellten Worker-Container wirksam. Ein bloßes `docker compose restart` lädt
keine geänderte Container-Umgebung. Mit denselben Compose-Dateien wie beim
Start kann nur der Corrector neu erstellt werden:

```bash
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  --profile voice-generative-corrector \
  up -d --force-recreate generative-corrector-worker
```

## 3. Stack starten

### Leichte Vosk-/Qwen-Variante

Für einen kleinen lokalen CPU-Pfad wird nur Vosk als ASR-Backend ausgewählt.
Ein geeigneter öffentlicher Corrector ist beispielsweise
`Qwen/Qwen2.5-0.5B-Instruct`; sein lokaler Katalogeintrag kann die ID
`qwen2.5-0.5b-instruct` verwenden. Modell und Katalog müssen wie oben beschrieben
lokal und auf eine unveränderliche Revision gepinnt vorliegen.

```bash
export VOICE_CPU_RECOGNITION_STRATEGY=single
export VOICE_CPU_BACKEND_FALLBACK_ORDER=vosk
export VOICE_CPU_CALIBRATION_PATH=''
export VOICE_CPU_FUSION_ENABLED=false
export VOICE_CPU_POLICY_ALLOWED_BACKENDS=vosk
export VOICE_CPU_POLICY_ALLOWED_RECOGNITION_STRATEGIES=single
export VOICE_CPU_SECONDARY_BACKENDS=''
export VOICE_CPU_RERUN_BACKEND=vosk
export VOICE_MAX_PARALLEL_BACKENDS=1
export VOICE_GENERATIVE_CORRECTOR_MODELS=qwen2.5-0.5b-instruct

docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  --profile voice-cpu \
  --profile voice-generative-corrector \
  up -d --build
```

Dieses Profil startet keinen Restricted-Inference-Dienst. Browser und Android
verwenden weiterhin ausschließlich den Hub. Voice Runtime und Corrector haben
getrennte Control-Plane-Netze; nur der Corrector darf zusätzlich die zentral
konfigurierten lokalen LLM-Endpunkte erreichen.

### Vollständiges CPU-Produktionsprofil

Vom Repository-Root wird zum CPU-Voice-Profil das additive Corrector-Profil
aktiviert:

```bash
INITIAL_ADMIN_PASSWORD='...' POSTGRES_PASSWORD='...' \
docker compose --env-file .env \
  -f docker/compose-next/compose.stack.full.yml \
  -f docker/compose-next/compose.voice-restricted.yml \
  --profile voice-production-cpu \
  --profile voice-generative-corrector \
  up -d --build
```

`voice-production-cpu` stellt Vosk/whisper.cpp und Restricted Inference bereit;
`voice-generative-corrector` ergänzt den separaten CPU-Corrector. In einer
Offline-/Produktionsumgebung werden geprüfte Images vorab gebaut und gestartet,
statt während des Deployments Abhängigkeiten zu laden.

Die relevanten Corrector-Defaults sind:

- `GENERATIVE_CORRECTOR_DEVICE=cpu`
- `GENERATIVE_CORRECTOR_MAX_IN_FLIGHT=1`
- `GENERATIVE_CORRECTOR_MAX_INPUT_CHARS=32000`
- `GENERATIVE_CORRECTOR_MAX_INPUT_TOKENS=4096`
- `GENERATIVE_CORRECTOR_MAX_NEW_TOKENS=1024`
- `GENERATIVE_CORRECTOR_MAX_REQUEST_BYTES=1048576`
- `VOICE_GENERATIVE_CORRECTOR_TIMEOUT_MS=30000`
- `VOICE_GENERATIVE_CORRECTOR_MAX_RESPONSE_BYTES=262144`
- `GENERATIVE_CORRECTOR_CPUS=4.0` und `GENERATIVE_CORRECTOR_MEMORY=8g`

CPU- und Speicherwerte sind Limits, keine Leistungszusage. Die gewählte lokal
unterstützte Gemma-/Phi-Variante muss mit realen Hardware-Benchmarks validiert
werden.

## 4. Capability prüfen

Nach der Anmeldung am Hub:

```bash
curl -s \
  -H "Authorization: Bearer <HUB_USER_TOKEN>" \
  http://localhost:5000/v1/voice/capabilities
```

Erwartet werden:

- `available: true` für den Voice-Pfad;
- ein verfügbarer Vosk-Eintrag im Voice-Modellkatalog;
- die Hub-Allowlist unter `correction_models`; ein Modell ist dort nur
  `available: true`/`status: ready`, wenn der Hub den streng erlaubten
  Worker-Health-Endpunkt erreicht, der Worker `ready` meldet und dessen Katalog
  genau diese Modell-ID enthält;
- `correction_providers` mit `embedded`, `ollama` und/oder `lmstudio` sowie
  `supports_manual_model`; URLs und Schlüssel sind in dieser Antwort nie enthalten;
- `correction_default` als wirksame allgemeine Provider-/Modellvorgabe für
  die Auswahl **Allgemeine LLM-Vorgabe**; Endpunkt und Zugangsdaten stammen
  weiterhin aus der beim Worker-Start übernommenen Deployment-Konfiguration;
- `generative_transcript_correction` in `capabilities`, sobald mindestens ein
  Corrector-Modell auf diese Weise als bereit verifiziert wurde;
- `privacy.raw_audio_persisted: false` mit dem Privacy-Default.

Der Worker selbst ist absichtlich nicht vom Host oder Client erreichbar. Sein
`/health` muss innerhalb des Containers `status=ready` und die erwarteten
`model_ids` melden. `degraded` bedeutet meistens: Token zu kurz/fehlend oder
Engine nicht konfiguriert. Ein ungültiger/fehlender Modellroot oder Katalog kann
den Worker bereits beim Start scheitern lassen; dann sind Containerstatus und
Logs zu prüfen.

## 5. Angular im Browser verwenden

1. Frontend öffnen, am Hub anmelden und zu `/voice` wechseln, zum Beispiel
   `http://localhost:4200/voice`.
2. Sprache, Profil-ID und optional eine Session-ID wählen.
3. Unter **Audioquelle** entweder **Mikrofon** oder
   **Lautsprecher / Systemaudio** wählen. Die Auswahl bleibt lokal im
   geöffneten Client und wird nicht mit dem Voice-Profil gespeichert.
4. Für den neuen klassischen Pfad `single` und `vosk` wählen.
5. **Nachträglich mit LLM verbessern** aktivieren.
6. Als Korrektur-Provider entweder **Allgemeine LLM-Vorgabe**, **Embedded**,
   **Ollama** oder **LM Studio** wählen. Bei Ollama/LM Studio erscheinen die
   vom laufenden Provider gemeldeten Modelle. Falls Discovery nicht möglich
   ist, kann bei einem vom Hub freigegebenen Provider **Modell-ID manuell
   eingeben** aktiviert werden. **Allgemeine LLM-Vorgabe** erbt nur Provider
   und Modell; für Endpoint- oder Zugangsdatenänderungen die Deployment-
   Variablen aus Abschnitt 2 setzen und den Corrector-Worker neu erstellen.
7. Die Erkennungs- und Korrekturauswahl für das Profil oder die konkrete
   Session speichern.
8. **Live** für kurze Vosk-Zwischentranskripte, **Langzeit bis 8 h** für einen
   beaufsichtigten rollierenden Run oder **Aufnehmen → transkribieren** für
   eine vollständige Aufnahme verwenden.

Für reine klassische Spracherkennung bleibt die LLM-Option ausgeschaltet; Vosk
und der Hub-eigene deterministische Pfad funktionieren ohne Corrector-Worker.
Im Browser benötigt Mikrofon-Capture einen sicheren Kontext (HTTPS oder
`localhost`) und die erteilte Mikrofonberechtigung.

### Beaufsichtigte Langzeit-Transkription

Der dritte Tab **Langzeit bis 8 h** verwendet die Hub-Endpunkte unter
`/v1/voice/live-runs`. Vor dem Start werden Segmentdauer (60, 90 oder 120
Sekunden) und eine maximale Laufzeit von einer, zwei, vier oder acht Stunden
gewählt. **Langzeit starten** öffnet die Berechtigungsabfrage für die gewählte
Audioquelle. Die Freigabe bleibt bis zum Stoppen oder bis zum konfigurierten
Zeitlimit geöffnet.

Der Hub speichert den Run als dauerhaften Parent-Task und jedes Segment als
delegierten Child-Task. Segmentstatus, bestätigte Sequenz, Lücken,
Zeitreferenzen und Ergebnisreferenzen werden dauerhaft gespeichert. Roh-Audio
wird nicht in der Hub-Datenbank persistiert; Transkript-Artefakte verwenden den
bestehenden verschlüsselten Voice-Artifact-Store. Überlappungen werden beim
fortlaufenden Zusammensetzen des Transkripts entfernt.

Noch nicht bestätigte Audiosegmente werden lokal als AES-GCM-Chiffretext in
IndexedDB gepuffert. Der Standardspeicher ist global auf fünf Segmente und
24 MiB begrenzt. Nach 24 Stunden sind Segmente logisch nicht mehr lesbar und
werden bei der nächsten Pufferoperation oder Initialisierung physisch gelöscht.
Ein bestätigtes Segment wird sofort gelöscht. Ist der Puffer voll oder nicht
sicher verfügbar, stoppt der Client fail-closed und markiert nicht
wiederherstellbare Sequenzen als Lücke, statt unbegrenzt Audio im Speicher zu
sammeln.

Nach einer kurzen Netz- oder Seitenunterbrechung zeigt die Oberfläche einen
Wiederherstellungshinweis. **Fortsetzen und Audio freigeben** übernimmt den
bestätigten Hub-Cursor und die verschlüsselten Restsegmente; die Audiofreigabe
muss aus Sicherheitsgründen erneut erteilt werden. **Puffer verwerfen** beendet
den Wiederherstellungspfad und löscht die lokalen Segmente. Beim Löschen eines
Voice-Profils wird auch dessen lokaler Langzeitpuffer entfernt.

Ist nur die Capture-Frist erreicht, der Hub-Run aber noch nicht abgelaufen,
fordert die Oberfläche keine neue Audiofreigabe mehr an. Stattdessen lädt
**Puffer hochladen und abschließen** die bereits aufgenommenen Chiffretext-
Segmente während der einstündigen Drain-Frist hoch und finalisiert den Run.
Nach `expires_at` werden keine Segmente mehr angenommen.

Für einen achtstündigen Run müssen Browser beziehungsweise Android-App offen
und der Hub sowie mindestens ein ASR-Worker dauerhaft erreichbar sein. Der
gewählte ASR-/Korrekturpfad muss ein Segment im Mittel schneller verarbeiten
als neue Segmente entstehen; andernfalls füllt sich der absichtlich kleine
Offline-Puffer. Außerdem muss die Hub-Anmeldung samt Refresh-Session für die
gesamte Laufzeit gültig bleiben. Der Angular-Client erneuert kurzlebige Hub-
Zugriffstoken automatisch und wiederholt eine wegen `401` abgewiesene Hub-
Anfrage genau einmal nach erfolgreichem Refresh. Wird die Session widerrufen
oder ist auch das Refresh-Token abgelaufen, ist kein zuverlässiger weiterer
Upload möglich und es können Segmentlücken entstehen.

Pro Profil und Run darf nur ein Ananta-Voice-Tab beziehungsweise eine App-
Instanz aktiv aufnehmen. Die Token-Erneuerung ist innerhalb einer Instanz
serialisiert; parallele Tabs werden wegen rotierender Refresh-Tokens derzeit
nicht als zuverlässiger Langzeitbetrieb unterstützt.

Diese Echtzeitfähigkeit ist vor langen produktiven Runs mit der konkreten
CPU/GPU und dem gewählten Modell zu messen. Der Modus ist beaufsichtigt:
Browser können Tabs drosseln, Betriebssysteme können Audiofreigaben beenden,
und Android kann einen App-Prozess bei Display-Aus oder Speicherdruck stoppen.
Heartbeat, Retry und Resume überbrücken begrenzte Störungen, ersetzen aber
keinen garantierten 24/7-Aufnahmedienst. Für unbeaufsichtigtes 24/7 wären ein
dedizierter Capture-Dienst, kontinuierliches Monitoring, Kapazitätsplanung und
eine explizite Aufbewahrungsrichtlinie nötig.

### Lautsprecher/Systemaudio im Browser

Bei gewähltem **Lautsprecher / Systemaudio** öffnet der Browser nach
**Live starten** beziehungsweise **Aufnahme starten** seinen Freigabedialog:

1. Den Tab, das Fenster oder den Bildschirm mit der gewünschten Wiedergabe
   auswählen.
2. Im Dialog **Audio teilen** aktivieren. Ohne freigegebene Audiospur bricht
   Ananta die Aufnahme mit einem Hinweis ab.
3. Die Wiedergabe starten und die Aufnahme anschließend in Ananta stoppen oder
   finalisieren. Wird die Browserfreigabe vorher beendet, endet auch die
   Audioaufnahme. Eine laufende Live-Session wird mit dem bis dahin empfangenen
   Audio automatisch finalisiert; eine Batch-Aufnahme bleibt lokal zum
   anschließenden Absenden bereit.

Systemaudio-Capture hängt vom Browser, Betriebssystem und der gewählten
Freigabefläche ab und benötigt ebenfalls einen sicheren Kontext. Der Browser
fordert die Freigabe für jede Aufnahme erneut an; sie wird nicht dauerhaft
erteilt. Die zugrunde liegende Browser-API verlangt technisch eine
Bildschirmspur. Ananta trennt davon ausschließlich die Audiospur ab: Bild und
Video werden weder aufgezeichnet noch verarbeitet oder an den Hub übertragen.

Im **Live**-Modus werden aus der freigegebenen Audioquelle während der Aufnahme
fortlaufend 16-kHz-Mono-Chunks über die bestehende Hub-Stream-API übertragen.
Im Modus **Aufnehmen → transkribieren** bleibt das aufgenommene Audio zunächst
lokal und wird erst mit **Über Hub transkribieren** als Datei an den bestehenden
Batch-Endpunkt gesendet.

Die Seite speichert bei aktivierter Korrektur den wirksamen Hub-Delta mit:

```json
{
  "transport_mode": "streaming",
  "recognition_strategy": "single",
  "primary_backend": "vosk",
  "correction_policy": "generative_rewrite",
  "review_policy": "always",
  "generative_corrector_provider": "ollama",
  "generative_corrector_model": "qwen2.5:7b",
  "feature_flags": {
    "generative_corrector": true
  }
}
```

Im Batch-Modus ist nur `transport_mode` entsprechend `batch`. Der optionale
Grenzwert `generative_corrector_max_edit_ratio` liegt standardmäßig bei `0.35`
und kann in der erweiterten Voice-Konfiguration gesetzt werden. Die
Konfigurationsreihenfolge ist
`defaults → legacy_global → global_delta → profile_delta → session_delta`; eine
Session-Auswahl überschreibt damit das Profil nur für diese Session.

Das Resultat zeigt das ursprüngliche ASR-Transkript, den korrigierten Text,
Änderungen, Modell-ID/-Revision und den erforderlichen Review. Bei
Worker-Ausfall, Timeout, nicht freigegebenem Modell, zu großem Änderungsanteil
oder geschützten Token-Änderungen bleibt das ursprüngliche ASR-Ergebnis erhalten
und der Hub kennzeichnet den Fallback.

## 6. Android verwenden

Die Capacitor-App verwendet dieselbe Route `/voice` und dieselben Hub-Profile.
Für beide nativen Capture-Pfade benötigt sie die Android-Audioberechtigung.
Für **Lautsprecher / Systemaudio** verwendet sie ab Android 10 (API-Level 29)
zusätzlich die Android-Wiedergabeaufnahme mit `MediaProjection`. Dafür erscheint
bei jeder Aufnahme ein Android-Systemdialog; erst nach ausdrücklicher Freigabe
startet der sichtbare MediaProjection-Dienst. Auf älteren Android-Versionen
weist die App beim Start darauf hin, dass Systemaudio nicht unterstützt wird.

Bei der leichten APK wird der Hub nicht auf dem Telefon ausgeführt. Auf der
Loginseite daher zuerst eine vom Gerät erreichbare **Hub-URL** speichern. Im
Android-Standardemulator ist das in der Regel `http://10.0.2.2:5000`; ein
physisches Gerät benötigt die LAN-Adresse des Hub-Rechners und eine passende
Firewall-Freigabe. Das Feld akzeptiert nur eine HTTP(S)-Origin ohne
Zugangsdaten, Pfad, Query oder Fragment.

- Mikrofon: Der native `VoiceCapture`-Adapter liest ausschließlich den
  Mikrofoneingang.
- Lautsprecher/Systemaudio: Der native `PlaybackAudioCapture`-Adapter liest die
  vom Android-System freigegebene Wiedergabe anderer Apps. Es werden keine
  Bildschirmbilder aufgezeichnet oder an den Hub übertragen.
- Live-Aufnahme: Der jeweilige Adapter erzeugt PCM16, 16 kHz, mono und sendet
  500-ms-Chunks in Reihenfolge an die Hub-Stream-API.
- Langzeit-Aufnahme: Derselbe PCM-Adapter bleibt geöffnet; Angular rotiert die
  Chunks in begrenzte WAV-Segmente und sendet sie über den Hub-Live-Run.
- Batch-Aufnahme: Der jeweilige Adapter puffert die PCM-Chunks lokal, erzeugt
  beim Stoppen WAV und sendet die Datei erst nach **Über Hub transkribieren**.

Android erlaubt Wiedergabeaufnahme nur innerhalb desselben Benutzerprofils und
nur für Apps und Audioarten, die Capture zulassen. Eine abspielende App kann
die Aufnahme im Manifest oder zur Laufzeit verbieten. Geschützte Medien,
Anrufe/Telefonie und andere nicht freigegebene Audioarten können deshalb stumm
bleiben und werden von Ananta nicht umgangen.

Der native Capture verwendet für die kurzen Live-/Batch-Modi weiterhin den
120-Sekunden-Default. Ausschließlich der Langzeit-Controller fordert eine
längere Laufzeit an; der native Adapter kappt sie hart bei 28 800 Sekunden.
Systemaudio läuft dabei in einem sichtbaren MediaProjection-Foreground-Service.
Die Mikrofonvariante läuft derzeit im App-Prozess: Für einen beaufsichtigten
achtstündigen Run müssen App und Gerät aktiv bleiben; zuverlässige Aufnahme im
Hintergrund oder bei ausgeschaltetem Display wird nicht zugesagt.

Für einen Hub auf einem anderen Rechner unter `/agents` den Eintrag `hub`
bearbeiten, seine vom Android-Gerät erreichbare URL eintragen und speichern,
zum Beispiel `https://ananta.example.org` oder im vertrauenswürdigen lokalen
Netz `http://192.168.1.20:5000`. Danach am konfigurierten Hub anmelden und
`/voice` öffnen. Eine explizit konfigurierte Remote-Hub-URL bleibt beim Start
der Android-App erhalten; `127.0.0.1:5000` bezeichnet dagegen immer das
Android-Gerät selbst. Bei einer Remote-Hub-URL ist der eingebettete lokale
Python-Hub keine Login-Voraussetzung und wird von diesem Startpfad übersprungen.

Auf dem Hub müssen die konkrete Android-/Capacitor-Origin, TLS und
Authentifizierung entsprechend der Deployment-Policy freigegeben sein. Weder
`voice-runtime:8090` noch `generative-corrector-worker:8093` wird als Agent oder
Client-URL eingetragen. Browser und Android adressieren ausschließlich den Hub.

## 7. Grenzen und Datenschutz

- Live-Audio verlässt das Gerät während der Aufnahme chunkweise; Batch-Audio
  erst nach dem expliziten Absenden. `VOICE_STORE_AUDIO=false` bleibt der
  fail-closed Default, dennoch werden Audio und Transkript zur Verarbeitung an
  den Hub übertragen.
- `raw_audio_persisted=false` bezeichnet fehlende Ananta-Retention nach dem
  Request. Während eines Segment-Requests können Multipart-Parser und ASR-
  Backend requestgebundene Temporary-Dateien verwenden. Das Voice-Compose-
  Profil hält `/tmp` dafür in einem begrenzten RAM-`tmpfs`; die Dateien werden
  beim Abschluss geschlossen und nicht als Ledger oder Artifact übernommen.
- Im Langzeitmodus verlässt jedes vollständige Segment das Gerät fortlaufend.
  Nur noch nicht bestätigte Segmente verbleiben zeitlich und mengenmäßig
  begrenzt als AES-GCM-Chiffretext in IndexedDB. Hub-Ledger und Parent-/Child-
  Tasks enthalten nur Orchestrierungsmetadaten und Ergebnisreferenzen, kein
  Roh-Audio und keinen Transkript-Klartext.
- Die Quellenwahl **Mikrofon** oder **Lautsprecher / Systemaudio** bleibt lokal
  konfiguriert und verändert weder Hub-Profil noch Session-Konfiguration. Bei
  einem Langzeit-Run wird die Quellenart als Orchestrierungsmetadatum an den
  Hub gesendet, damit Resume und Audit den Capture-Pfad eindeutig beschreiben;
  Bildschirm- oder Audioinhalt wird dadurch nicht zusätzlich gespeichert.
- Systemaudio ist keine Umgehung von Plattform- oder Inhaltsschutz. Browser
  und Android können je nach Freigabe, App-Opt-out, DRM oder Audioart keine
  Audiospur liefern; insbesondere Anrufaudio ist nicht als aufnehmbar zugesagt.
- Das ASR-Transkript wird für den aktivierten Korrekturschritt intern an den
  Corrector-Worker delegiert. Für `embedded` liest er ausschließlich den
  read-only Modellmount; für `ollama`/`lmstudio` sendet er den Text an den
  zentral konfigurierten lokalen Provider. Ein Client kann dieses Ziel nicht
  überschreiben.
- Das LLM ist kein Wahrheitsprüfer. Der Worker begrenzt den Änderungsanteil und
  schützt unter anderem URLs und Token mit Ziffern; semantische Fehler sind
  weiterhin möglich. Deshalb erzwingt `generative_rewrite` den Review-Modus
  `always`.
- Ein Modellwechsel ist eine Hub-Policy-Änderung. Clients reichen nur Provider-
  und Modell-ID als Profil-/Session-Auswahl ein und dürfen weder Worker-Endpunkt,
  Provider-URL, Schlüssel noch lokalen Modellpfad festlegen.

Weiterführend:

- [Voice/Restricted Production Runbook](operations/voice-restricted-production-runbook.md)
- [Voice Runtime Privacy Defaults](voice-runtime-privacy.md)
- [Voice Runtime Architecture](voice-runtime-architecture.md)
- [Voice Runtime Limitations](voice-runtime-limitations.md)
