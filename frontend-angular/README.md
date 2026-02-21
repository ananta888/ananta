# Angular SPA

## Lokale Entwicklung
```bash
cd frontend-angular
npm install
npm start
```

App: `http://localhost:4200`

## E2E-Tests
```bash
npm run test:e2e
npm run test:e2e:lite
npm run test:e2e:live
```

Wichtige E2E-Umgebungsvariablen:
- `E2E_PORT`: setzt den Port fuer den Test-Dev-Server (hilft bei parallelen Runs oder Port-Konflikten).
- `E2E_REUSE_SERVER=1`: nutzt einen bereits laufenden Dev-Server wieder. Standard ist **aus** (frischer Server), um stale Bundles zu vermeiden.
- `E2E_REPORTER_MODE=compact`: reduziert Konsolenrauschen (`dot`) und schreibt zusaetzlich `test-results/results.json`.
- `E2E_LITE_TIMEOUT_MINUTES`: Timeout fuer `npm run test:e2e:lite` (Default: `25`).
- `RUN_LIVE_LLM_TESTS=1`: aktiviert Live-LMStudio-Tests.

Optional mehrere Browser:
```bash
E2E_BROWSERS=chromium,firefox,webkit npm run test:e2e
```

PowerShell-Beispiele:
```powershell
$env:E2E_PORT="4303"; npm run test:e2e
$env:E2E_REUSE_SERVER="1"; npm run test:e2e
$env:E2E_LITE_TIMEOUT_MINUTES="35"; npm run test:e2e:lite
$env:RUN_LIVE_LLM_TESTS="1"; npm run test:e2e:live
```

## Hinweise
- Standard-CI fuehrt regulaeere Playwright-Tests aus.
- Live-LLM-Tests sind separiert und werden gezielt gestartet.
- Frontend basiert auf Angular 21 (siehe `package.json`).

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
- JDK 17+
- Android Studio (inkl. Android SDK + Build Tools)
- Akzeptierte SDK Lizenzen

### Hinweis zum aktuellen Projektstatus
Falls `npm run android:prepare` beim Angular-Build fehlschlägt, liegt es derzeit an bestehenden TypeScript-Fehlern im Frontend-Code. Capacitor selbst ist korrekt integriert; nach Behebung der TS-Fehler läuft der Android-Workflow direkt weiter.
