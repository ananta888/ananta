# Angular SPA

## Lokale Entwicklung
```bash
cd frontend-angular
npm install
npm start
```

App: `http://localhost:4200`

## Voice: Mikrofon oder Lautsprecher/Systemaudio

Die Route `/voice` bietet für **Live** und **Aufnehmen → transkribieren** eine
gerätelokale Quellenwahl:

- **Mikrofon** verwendet den Mikrofoneingang.
- **Lautsprecher / Systemaudio** nimmt die vom Browser beziehungsweise von
  Android ausdrücklich freigegebene Wiedergabe auf.

Die Quellenwahl bleibt lokal im geöffneten Client und wird nicht in einem
Hub-Profil oder Session-Delta gespeichert. Sie ändert weder die Voice-API noch
ASR-, Korrektur- oder Routing-Auswahl. Browser und Android senden weiterhin
ausschließlich an die bestehenden Hub-Endpunkte; kein Client ruft die
Voice-Runtime direkt auf.

### Web

Bei **Lautsprecher / Systemaudio** öffnet **Live starten** oder
**Aufnahme starten** den Browser-Freigabedialog. Dort den gewünschten Tab, das
Fenster oder den Bildschirm wählen und **Audio teilen** aktivieren. Ohne eine
freigegebene Audiospur wird die Aufnahme abgebrochen. Unterstützung und
verfügbare Audioquellen hängen von Browser und Betriebssystem ab; ein sicherer
Kontext (HTTPS oder `localhost`) ist erforderlich.

Die Browser-API benötigt technisch eine Bildschirmspur für den Dialog. Ananta
trennt davon nur die Audiospur ab. Bild und Video werden niemals aufgezeichnet,
verarbeitet oder an den Hub übertragen. Die Freigabe muss für jede Aufnahme
neu bestätigt werden und endet auch, wenn sie über die Browseranzeige gestoppt
wird. In diesem Fall finalisiert Ananta eine laufende Live-Session mit dem bis
dahin empfangenen Audio; eine Batch-Aufnahme bleibt lokal zum Absenden bereit.

### Android

Systemaudio steht ab Android 10 (API-Level 29) zur Verfügung. Die App verwendet
dafür die Android-Audioberechtigung und `MediaProjection` und zeigt für jede
Aufnahme den Systemdialog sowie während der Aufnahme den zugehörigen
Vordergrunddienst an. Es werden nur Audiodaten übernommen; Bildschirmbilder
werden nicht aufgezeichnet oder übertragen. Unterhalb von Android 10 lehnt die
App den Start mit einem klaren Hinweis ab.

Android kann nur Wiedergabe aus demselben Benutzerprofil aufnehmen, wenn die
abspielende App und die konkrete Audioart Capture erlauben. App-Opt-out,
geschützte/DRM-Inhalte, Anrufe und Telefonie können stumm bleiben; Ananta umgeht
diese Plattformgrenzen nicht.

Die native Aufnahme endet nach spätestens 120 Sekunden. Live wird dann mit dem
bis dahin übertragenen Audio automatisch finalisiert; Batch bleibt lokal zum
anschließenden Absenden bereit.

### Übertragung

- **Live:** Die gewählte Quelle wird während der Aufnahme in geordneten
  PCM16-/16-kHz-Mono-Chunks an die Hub-Stream-API gesendet.
- **Aufnehmen → transkribieren:** Die Aufnahme bleibt zunächst auf dem Gerät.
  Erst **Über Hub transkribieren** sendet die Audiodatei an den bestehenden
  Batch-Endpunkt.

Ausführliche Einrichtung und Bedienung stehen im
[Voice Quickstart](../docs/voice-quickstart.md#5-angular-im-browser-verwenden).

## E2E-Tests
```bash
npm run test:e2e
npm run test:e2e:lite
npm run test:e2e:live
npm run test:e2e:compose
npm run test:e2e:live:compose
npm run test:e2e:android:terminal
npm run test:e2e:android:terminal:bootstrap
```

Wichtige E2E-Umgebungsvariablen:
- `E2E_PORT`: setzt den Port fuer den Test-Dev-Server (hilft bei parallelen Runs oder Port-Konflikten).
- `E2E_REUSE_SERVER=1`: nutzt einen bereits laufenden Dev-Server wieder. Standard ist **aus** (frischer Server), um stale Bundles zu vermeiden.
- `E2E_REPORTER_MODE=compact`: reduziert Konsolenrauschen (`dot`) und schreibt zusaetzlich `test-results/results.json`.
- `E2E_LITE_TIMEOUT_MINUTES`: Timeout fuer `npm run test:e2e:lite` (Default: `25`).
- `RUN_LIVE_LLM_TESTS=1`: aktiviert Live-LLM-Tests.
- `LIVE_LLM_PROVIDER=ollama`: standardisiert Live-E2E auf den Compose-Ollama-Service.
- `OLLAMA_URL`: expliziter Ollama-Endpoint, z. B. `http://localhost:11434/api/generate` oder in Compose `http://ollama:11434/api/generate`.

Optional mehrere Browser:
```bash
E2E_BROWSERS=chromium,firefox,webkit npm run test:e2e
```

PowerShell-Beispiele:
```powershell
$env:E2E_PORT="4303"; npm run test:e2e
$env:E2E_REUSE_SERVER="1"; npm run test:e2e
$env:E2E_LITE_TIMEOUT_MINUTES="35"; npm run test:e2e:lite
$env:RUN_LIVE_LLM_TESTS="1"; $env:LIVE_LLM_PROVIDER="ollama"; $env:OLLAMA_URL="http://localhost:11434/api/generate"; npm run test:e2e:live
```

Android-Emulator (echtes APK/E2E fuer Live-Terminal):
```bash
ANANTA_ANDROID_AVD_NAME=ananta-api35 npm run test:e2e:android:terminal
```

Vollautomatisch (SDK/AVD installieren + Stack starten + Test ausfuehren):
```bash
ANANTA_ANDROID_AVD_NAME=ananta-api35 npm run test:e2e:android:terminal:bootstrap
```

Docker-Variante (Android-SDK/Emulator im Container-Image vorinstalliert, Source zur Laufzeit gemountet):
```bash
ANANTA_ANDROID_AVD_NAME=ananta-api35 npm run test:e2e:android:terminal:bootstrap:docker
```

GitHub Actions:
- Workflow `Android Delivery APK` baut bei Push/PR (aenderungen unter `frontend-angular/**`) automatisch die Delivery-APK via `scripts/build-android-delivery-apk.sh`.
- Artifact-Name: `ananta-delivery-proot-voxtral-debug-apk` (enthaelt APK + `.sha256`).

Wichtige Variablen dafuer:
- `ANANTA_ANDROID_AVD_NAME` (Default: `ananta-api35`)
- `ANANTA_ANDROID_EMULATOR_SERIAL` (Default: `emulator-5554`)
- `ANANTA_E2E_ADMIN_USER` / `ANANTA_E2E_ADMIN_PASSWORD`
- `ANANTA_ANDROID_REVERSE_PORTS` (Default: `4200 5500 5501 5502 11434`)

## Hinweise
- Standard-CI fuehrt regulaeere Playwright-Tests aus.
- Live-LLM-Tests laufen standardisiert gegen die Compose-Welt mit Ollama.
- Frontend basiert auf Angular 21 (siehe `package.json`).
- Markdown Slides sind unter `/markdown-slides` erreichbar. Details zu Syntax, Sicherheit, Artefakten und Export-Grenzen stehen in `../docs/markdown-slides.md`.

## Sichere Migrationen
Siehe docs/angular-migration-safety-workflow.md fuer den schrittweisen Schematics-Workflow.

## Android App mit Capacitor

Capacitor ist im Projekt integriert.

### Einmalig einrichten
```bash
cd frontend-angular
npm install
```

### Android-Projekt aktualisieren
```bash
npm run android:prepare
```

Das macht:
1. Angular Build (`npm run build:android`)
2. Web-Assets in das native Android-Projekt synchronisieren (`npm run cap:sync`)

### Debug-APK direkt per CLI (ARM64/Proot Host)
Auf ARM64-Hosts (z. B. Android/Termux-Proot) kann Gradle ein x86_64-`aapt2` laden. Dann muss `aapt2` via qemu uebersteuert werden:

```bash
cd frontend-angular
npm run android:prepare

AAPT2_BIN="$(find /root/.gradle/caches -type f -path '*aapt2-*-linux/aapt2' | head -n 1)"
cat >/tmp/aapt2 <<EOF
#!/usr/bin/env sh
export QEMU_LD_PREFIX=/usr/x86_64-linux-gnu
exec qemu-x86_64 "$AAPT2_BIN" "$@"
EOF
chmod +x /tmp/aapt2

cd android
./gradlew :app:assembleDebug --no-daemon -Pandroid.aapt2FromMavenOverride=/tmp/aapt2
```

APK-Pfad:
`frontend-angular/android/app/build/outputs/apk/debug/app-debug.apk`

### Persistente App-Daten bei Deploys (Modelle nicht neu laden)

Damit lokale Modelle/Runner nach `adb install -r` erhalten bleiben, muss die APK bei Folgebuilds mit demselben Signatur-Key gebaut werden.

- Build nutzt bevorzugt einen stabilen Debug-Keystore:
  1. `-PanantaDebugKeystorePath=/pfad/zur/debug.keystore`
  2. `ANANTA_DEBUG_KEYSTORE_PATH=/pfad/zur/debug.keystore`
  3. Fallbacks: `/mnt/c/Users/pst/.android/debug.keystore`, dann `~/.android/debug.keystore`
- Deploy immer als Update: `adb install -r ...`
- Nur bei `INSTALL_FAILED_UPDATE_INCOMPATIBLE` ist ein einmaliges Uninstall noetig; dabei gehen App-Daten (inkl. heruntergeladener Modelle) verloren.

### Reproduzierbare Auslieferungs-APK mit Proot/Voxtral-Defaults

Die Auslieferungs-APK wird mit den gebuendelten Proot/Ubuntu- und Workspace-Seeds sowie dem passenden Voxtral-Realtime-Runner gebaut. Der LLM-Server (`llama.cpp` `b8994`) ist ebenfalls in der APK enthalten und wird in der LLM-Runtime ohne externen Server-Download installiert. LLM/Voxtral-Modelle werden dabei **nicht** eingebettet; das Standardmodell `Voxtral Mini 4B Realtime Q2_K` wird im UI als Download angeboten.

```bash
cd frontend-angular
./scripts/build-android-delivery-apk.sh
```

Output:
`frontend-angular/android/app/build/outputs/apk/debug/ananta-delivery-proot-voxtral-debug.apk`

Wenn die Seed-Assets neu aus einer vorbereiteten Android-App-Sandbox erzeugt werden sollen:

```bash
cd frontend-angular
ANANTA_ADB_CONNECT=192.168.x.x:PORT \
ANANTA_EXPORT_ANDROID_SEED=1 \
./scripts/build-android-delivery-apk.sh
```

Voraussetzungen fuer den Seed-Export:
- Die App-Sandbox enthaelt `files/proot-runtime/distros/ubuntu/rootfs/ubuntu-questing-aarch64`.
- `files/ananta` ist installiert.
- Python, pip, git, curl, libgomp, `ananta`, `ananta tui` und `ananta-worker` sind im Rootfs bereit.
- Auf ARM64/Termux-Proot nutzt das Script automatisch `/tmp/aapt2`, wenn vorhanden, oder erzeugt einen qemu-basierten Wrapper aus dem Gradle-Cache.

Wenn der gebuendelte Voxtral-Runner neu erzeugt werden soll:

```bash
cd frontend-angular
ANANTA_BUILD_VOXTRAL_RUNNER=1 ./scripts/build-android-delivery-apk.sh
```

### Android Studio öffnen
```bash
npm run cap:open:android
```

### APK / AAB für Play Store
In Android Studio:
1. `Build` -> `Generate Signed Bundle / APK`
2. `Android App Bundle (AAB)` wählen
3. Keystore anlegen/auswählen
4. `release` signieren und exportieren

Für den Play Store ist das empfohlene Artefakt ein `AAB`.

### Voraussetzungen (Host-System)
- JDK 21 (`@capacitor/android` 7.5 kompiliert mit Java 21)
- Android Studio (inkl. Android SDK + Build Tools)
- Akzeptierte SDK Lizenzen

### Leichte Remote-Hub-APK

Die kleine Variante verwendet die Angular-Voice-Oberfläche und den nativen
Mikrofon-Adapter, führt Vosk und die optionale LLM-Korrektur aber weiterhin über
den Hub aus. Sie enthält weder die eingebettete Python- noch die llama.cpp-Runtime:

```bash
npm run android:prepare
cd android
./gradlew --no-daemon \
  -PanantaEnablePythonRuntime=false \
  -PanantaEnableLlamaCppRuntime=false \
  :app:assembleDebug
```

Das Ergebnis liegt unter `android/app/build/outputs/apk/debug/app-debug.apk`.
Auf der Android-Loginseite muss ein erreichbarer Hub-Origin eingetragen werden:
im Standard-Emulator typischerweise `http://10.0.2.2:5000`, auf einem echten
Gerät die LAN-Adresse des Hub-Rechners. Benutzername oder Token gehören nicht
in die URL.

## Voxtral Offline in der App

In der nativen Android-App gibt es die Route `/voxtral-offline` mit:
- Modell-Presets, mit `Voxtral Mini 4B Realtime Q2_K` (<1.4 GiB) als Standard fuer Nutzer
- Mikrofon-Permission anfragen
- WAV-Aufnahme (16 kHz mono) im App-Storage starten/stoppen
- Modell direkt in App-Storage herunterladen
- Runner-Binary direkt in App-Storage herunterladen (wird ausfuehrbar gesetzt)
- Passenden Voxtral-Runner bereitstellen: baut den kompatiblen Voxtral-Realtime-Runner aus dem fest verdrahteten funktionierenden Source-Stand
- Runner-Archive (`.tar.gz`) werden beim Download automatisch entpackt und ein passender Runner extrahiert
- Lokale Modelle/Runner auflisten und auswaehlen
- Setup-Pruefung (Speicher, Modell vorhanden, Runner ausfuehrbar)
- Lokale Offline-Transkription durch Runner-Aufruf via nativer Android-Bridge
- Live-Modus (Chunk-basiert): fortlaufende Teiltranskripte im UI

Hinweis: Fuer produktive Play-Store-Auslieferung ist als naechster Schritt eine feste NDK/JNI-Integration empfehlenswert. Der aktuelle Stand nutzt einen lokalen Runner-Binary-Pfad innerhalb der App.

## Embedded Python Runtime (Hub/Worker)

Die App enthaelt eine Python-Runtime (Chaquopy) fuer lokalen Hub/Worker-Betrieb:
- Android Plugin: `PythonRuntime` (Start/Stop/Status/Health)
- Python Entry-Points: `android/app/src/main/python/ananta_runtime.py`
- UI-Seite: Route `/python-runtime`
- Delivery-APK kann Ubuntu + Ananta-Workspace inkl. Worker-Abhaengigkeiten (python3/pip/libgomp/ananta-worker) als Seed-Assets mitliefern.
- `opencode` wird bewusst **nicht** im Seed ausgeliefert und bleibt ein On-Demand-Download (`installOpencode`).

## LLM Runtime in der App

Route `/llama-runtime` bietet:
- Vorinstallierten llama.cpp-Server aus der APK (kein initialer Server-Netzwerkdownload notwendig).
- Modell-Presets fuer Android-taugliche Groessen (bis ca. 2 GB).
- Frei konfigurierbare Modell-Installation aus beliebigen `.gguf`-Quellen (URL + Dateiname, optional SHA256).
- Auswahl und Wechsel des aktiven lokal installierten Modells.

Standardmaessig ist die Python-Runtime fuer Android-Builds aktiviert.

Steuerung in `frontend-angular/android/gradle.properties`:
```properties
anantaEnablePythonRuntime=true
anantaPythonVersion=3.11
```

Danach wie gewohnt:
```bash
cd frontend-angular
npm run android:prepare
```
