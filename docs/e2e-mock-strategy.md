# E2E Mock Strategy

## Ziel
E2E-Tests sollen Nutzerfluesse stabil pruefen, nicht externe Zufaelle (Latenzen, Timeout-Spikes, volatile Daten).

## Grundregeln
1. Kritische Kernfluesse mit echter API testen (Login, Navigation, zentrale Saves).
2. Nicht-deterministische Integrationspunkte mocken (z. B. Trigger-Test-Endpunkte, externe Provider).
3. Pro Test genau dokumentieren, was gemockt wird und warum.
4. Mocks nur testlokal setzen (`test`/`beforeEach`) und nicht global fuer alle Specs.

## Wann mocken?
- API-Aufrufe mit bekannten Timeouts/Schwankungen.
- Externe Systeme, die nicht Teil des Repos sind.
- Pfade, die nur UI-Verhalten verifizieren (Button-State, Rendering, Error-Feedback).

## Wann nicht mocken?
- Auth-Basisfluss.
- Persistenz-/Konfigurationspfade, die wir bewusst End-to-End absichern wollen.
- Routing/Navigation innerhalb der App.

## Technischer Standard
- Helper verwenden: `frontend-angular/tests/helpers/mock-http.ts`
- Beispiel:
```ts
await mockJson(page, '**/triggers/test', { ok: true, would_create: 1 });
```

## CI-Hinweis
Bei reproduzierbaren Flakes zuerst pruefen:
1. Ist der Test ein UI-Test? Dann mocken.
2. Ist es ein Integrationsvertrag? Dann retry/timeout und Cleanup robust machen.

