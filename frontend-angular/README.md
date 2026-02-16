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
npm run test:e2e:live
```

Wichtige E2E-Umgebungsvariablen:
- `E2E_PORT`: setzt den Port fuer den Test-Dev-Server (hilft bei parallelen Runs oder Port-Konflikten).
- `E2E_REUSE_SERVER=1`: nutzt einen bereits laufenden Dev-Server wieder. Standard ist **aus** (frischer Server), um stale Bundles zu vermeiden.
- `RUN_LIVE_LLM_TESTS=1`: aktiviert Live-LMStudio-Tests.

Optional mehrere Browser:
```bash
E2E_BROWSERS=chromium,firefox,webkit npm run test:e2e
```

PowerShell-Beispiele:
```powershell
$env:E2E_PORT="4303"; npm run test:e2e
$env:E2E_REUSE_SERVER="1"; npm run test:e2e
$env:RUN_LIVE_LLM_TESTS="1"; npm run test:e2e:live
```

## Hinweise
- Standard-CI fuehrt regulaeere Playwright-Tests aus.
- Live-LLM-Tests sind separiert und werden gezielt gestartet.
- Frontend basiert auf Angular 21 (siehe `package.json`).

## Sichere Migrationen
Siehe docs/angular-migration-safety-workflow.md fuer den schrittweisen Schematics-Workflow.
