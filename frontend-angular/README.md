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

Optional mehrere Browser:
```bash
E2E_BROWSERS=chromium,firefox,webkit npm run test:e2e
```

## Hinweise
- Standard-CI fuehrt regulaeere Playwright-Tests aus.
- Live-LLM-Tests sind separiert und werden gezielt gestartet.
- Frontend basiert auf Angular 21 (siehe `package.json`).