# Smart-/Dumb-Komponenten Leitfaden

## Ziel
Komplexe UI-Logik in klar trennbare Verantwortungen aufteilen:
- **Smart-Komponenten**: laden/speichern Daten, orchestrieren Use-Cases.
- **Dumb-Komponenten**: zeigen Daten an und emittieren Events.

## Verbindliche Regeln
1. HTTP/API-Aufrufe nur in Smart-Komponenten oder Services.
2. Dumb-Komponenten bekommen Daten nur ueber `@Input`.
3. Dumb-Komponenten geben Nutzeraktionen nur ueber `@Output` zurueck.
4. Keine Router-/Storage-Logik in Dumb-Komponenten.
5. Business-Validierung in Services oder Smart-Komponenten, nicht im Template.

## Namenskonvention
- `*.container.ts` fuer Smart-Komponenten
- `*.view.ts` oder `*.presenter.ts` fuer Dumb-Komponenten

## Refactoring-Reihenfolge (empfohlen)
1. `settings.component.ts`:
   - Section-Switching im Container
   - Teilansichten (LLM, Qualitaetsregeln, System) als Dumb-Komponenten
2. `webhooks.component.ts`:
   - URL/Secret/Test-Views abtrennen
3. `operations-console.component.ts`:
   - Queue-Metriken, Task-Tabelle, Ingest-Form als einzelne Views

## Checkliste pro Refactoring
- Inputs/Outputs definiert und getestet
- Keine API-Calls im View
- Unit-Tests fuer View-Logik (Rendering/Events)
- E2E-Locators (`data-testid`) bleiben stabil

