# UI/UX Copywriting Guide

## Sprache
- Primärsprache ist Deutsch.
- Kurze, handlungsorientierte Verben verwenden: `Speichern`, `Aktualisieren`, `Abbrechen`, `Loeschen`.
- Keine Mischung aus Deutsch/Englisch innerhalb derselben View.

## Buttons
- Primäraktion eindeutig benennen, z. B. `Task erstellen`.
- Sekundäraktionen neutral halten, z. B. `Zurueck`, `Details`.
- Verben im Imperativ, keine technischen Begriffe ohne Kontext.

## Status und Feedback
- Erfolg: `Gespeichert`, `Aktualisiert`, `Erfolgreich ausgefuehrt`.
- Fehler: `Konnte nicht gespeichert werden` plus konkreter Grund.
- Ladezustand: `Wird geladen...` oder `Speichere...`.

## Begriffe
- `Agenten` statt `Agents`
- `Einstellungen` statt `Settings`
- `Audit-Logs` statt `Audit Logs`
- `Aktualisieren` statt `Refresh`

## Blueprint/Template/Team Wortwahl
- In Standard-Views bevorzugen: `Start-Aufgaben`, `Work Roles`, `Team-Start`, `Erwartete Outputs`.
- Interne Begriffe wie `snapshot`, `drift`, `reconcile` nur in Admin-/Studio-Kontexten prominent zeigen.
- Immer klar trennen:
  - `Rollen-Templates` = Rollenverhalten
  - `Blueprints` = Team-Struktur und Startzuschnitt
  - `Teams` = laufende Instanz

## Accessibility
- Alle interaktiven Elemente mit klaren `aria-label`/sichtbaren Labels.
- Keine rein ikonischen Buttons ohne Text oder Label.
