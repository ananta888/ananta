# Voice Quickstart: Vosk + wählbare lokale LLM-Korrektur

Dieser Quickstart beschreibt den gemeinsamen Voice-Pfad für Browser und die
Capacitor-Android-App. Beide Clients verwenden die Angular-Seite `/voice` und
sprechen ausschließlich mit dem Hub. Der Hub delegiert klassische
Spracherkennung an `voice-runtime` und die optionale Textkorrektur an den
isolierten `generative-corrector-worker`.

Die beiden Bedienmodi sind:

- **Live:** 16-kHz-Mono-PCM wird in geordneten Chunks an den Hub gesendet. Vosk
  liefert während der Aufnahme Zwischentranskripte. Eine gewählte Gemma-, Phi-
  oder andere freigegebene LLM-Variante korrigiert erst das finale Transkript.
- **Aufnehmen → transkribieren:** Die Aufnahme bleibt bis zum Absenden auf dem
  Gerät und wird anschließend als eine Batch-Anfrage über den Hub verarbeitet.

Eine LLM-Korrektur läuft niemals für jeden Live-Chunk. Sie ist ein begrenzter,
nachgelagerter Text-zu-Text-Schritt beim Finalisieren oder nach der
Batch-Transkription.

## 1. Lokale Modelle bereitstellen

Das CPU-Profil erwartet den bestehenden Voice-Modellbaum mit einem Vosk-Modell
unter `models/voice/vosk` und dem Voice-Katalog unter
`models/voice/manifests/voice-models.json`. Details zur Voice-Modellpromotion
stehen im
[Production Runbook](operations/voice-restricted-production-runbook.md#model-promotion).

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

Jede ID der Hub-Allowlist `VOICE_GENERATIVE_CORRECTOR_MODELS` muss exakt im
Worker-Katalog vorkommen. Zusätzliche Katalogmodelle bleiben für Clients
unsichtbar; für Least Privilege sollten beide Mengen in Produktion bewusst
deckungsgleich gehalten werden. Um ein anderes Modell anzubieten, wird es daher
sowohl in den read-only Katalog als auch in diese kommagetrennte Hub-Allowlist
aufgenommen. Die Modellgewichte selbst gehören nicht in das Repository.

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

export VOICE_PERSONALIZATION_ENCRYPTION_KEY="$(${PYTHON:-python3} -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

Secrets gehören in den Deployment-Secret-Store und niemals in Git, Browser-
Storage, Android-Ressourcen oder URLs. Das Angular-/Android-Login verwendet ein
normales Hub-Benutzertoken; es erhält keines der internen Runtime-Tokens.

## 3. Stack starten

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
3. Für den neuen klassischen Pfad `single` und `vosk` wählen.
4. **Nachträglich mit LLM verbessern** aktivieren und eines der vom Hub als
   verfügbar gemeldeten Modelle auswählen.
5. Die Auswahl für das Profil oder die konkrete Session speichern.
6. **Live** für Vosk-Zwischentranskripte oder
   **Aufnehmen → transkribieren** für eine vollständige Aufnahme verwenden.

Für reine klassische Spracherkennung bleibt die LLM-Option ausgeschaltet; Vosk
und der Hub-eigene deterministische Pfad funktionieren ohne Corrector-Worker.
Im Browser benötigt Mikrofon-Capture einen sicheren Kontext (HTTPS oder
`localhost`) und die erteilte Mikrofonberechtigung.

Die Seite speichert bei aktivierter Korrektur den wirksamen Hub-Delta mit:

```json
{
  "transport_mode": "streaming",
  "recognition_strategy": "single",
  "primary_backend": "vosk",
  "correction_policy": "generative_rewrite",
  "review_policy": "always",
  "generative_corrector_model": "gemma-2b-it",
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
Sie benötigt die Android-Mikrofonberechtigung:

- Live-Aufnahme: Der native `VoiceCapture`-Adapter erzeugt PCM16, 16 kHz, mono
  und sendet 500-ms-Chunks in Reihenfolge an die Hub-Stream-API.
- Batch-Aufnahme: Derselbe Capture-Adapter puffert die PCM-Chunks lokal, erzeugt
  beim Stoppen WAV und sendet die Datei erst nach **Über Hub transkribieren**.

Der aktuelle native Capture begrenzt eine Aufnahme auf 120 Sekunden. Längere
Aufnahmen müssen als bereits vorhandene, vom Hub akzeptierte Audiodatei über den
Batch-Pfad eingereicht oder in mehrere Sessions aufgeteilt werden.

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
- Das ASR-Transkript wird für den aktivierten Korrekturschritt intern an den
  Corrector-Worker delegiert. Dieser besitzt nur sein read-only Modellmount,
  ein begrenztes `tmpfs` und ein internes Netz zum Hub.
- Das LLM ist kein Wahrheitsprüfer. Der Worker begrenzt den Änderungsanteil und
  schützt unter anderem URLs und Token mit Ziffern; semantische Fehler sind
  weiterhin möglich. Deshalb erzwingt `generative_rewrite` den Review-Modus
  `always`.
- Ein Modellwechsel ist eine Hub-Policy-Änderung. Clients reichen nur die
  Profil-/Session-Auswahl ein und dürfen weder Worker-Endpunkt noch lokalen
  Modellpfad festlegen.

Weiterführend:

- [Voice/Restricted Production Runbook](operations/voice-restricted-production-runbook.md)
- [Voice Runtime Privacy Defaults](voice-runtime-privacy.md)
- [Voice Runtime Architecture](voice-runtime-architecture.md)
- [Voice Runtime Limitations](voice-runtime-limitations.md)
