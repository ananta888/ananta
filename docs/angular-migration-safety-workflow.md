# Angular Migrations: Safety Workflow

Dieses Dokument beschreibt einen sicheren Ablauf fuer Schematics/Codemods im Frontend.

## Ziel
- Grosse automatische Migrationen (z. B. `inject()` oder `@if/@for`) in kleinen, rueckrollbaren Schritten umsetzen.
- Build- und Lint-Breaks frueh erkennen.

## Ablauf pro Migrationsschritt
1. Scope klein halten (z. B. nur `src/app/services` oder einzelne Komponenten-Ordner).
2. Migration nur fuer den Scope ausfuehren.
3. Sofort validieren:
```bash
npm run lint
npm run build
```
4. Bei Fehlern sofort rueckgaengig machen (`git restore <betroffene-dateien>`), Scope weiter verkleinern.
5. Erst nach gruener Validierung den naechsten Scope migrieren.

## Empfohlene Reihenfolge
1. Services/Interceptors
2. Utility-nahe Komponenten
3. Komplexe Container-Komponenten
4. Templates (`@if/@for`) erst nach stabiler TS-Migration

## Befehlsbeispiele
```bash
# 1) DI-Migration fuer Services
npx ng generate @angular/core:inject --path src/app/services

# 2) Kontrollfluss-Migration fuer kleine Teilbereiche
npx ng generate @angular/core:control-flow --path src/app/components/<bereich>

# 3) Validierung
npm run lint
npm run build
```

## Abschluss
- Temporaer deaktivierte ESLint-Regeln wieder aktivieren:
  - `@angular-eslint/prefer-inject`
  - `@angular-eslint/template/prefer-control-flow`
- Danach final `npm run lint` und `npm run build` ausfuehren.