# UI/UX Regression Checklist

## Empfohlene Stufenabfolge
- Stufe A: `main-goal-foundation.spec.ts`
- Stufe B: `main-goal-planning.spec.ts`
- Stufe C: `main-goal-execution.spec.ts`
- Stufe D: `main-goal-observability.spec.ts`
- Sichtbarkeit/Gates: `ui-ux-console.spec.ts` und Live-Klicklauf (`firefox_live_click_extended.py`)

## Live-Beobachtung (Firefox-VNC)
- Browser im Test-Netz starten: `scripts/start-firefox-vnc.sh start`
- noVNC oeffnen: `http://localhost:7900` (Passwort: `secret`)
- Slow-Mode-Lauf: `python3 scripts/firefox_live_click_extended.py --step-delay-seconds 1.5`
- Replay eines frueheren Laufs: `python3 scripts/firefox_live_click_extended.py --replay-from-report <report.json>`
- Harte Fehler-Gates aktiv lassen; nur fuer Diagnose optional `--allow-visible-errors`.

## Navigation
- Hauptnavigation auf Desktop und Mobile konsistent.
- Keine doppelten, widersprüchlichen Menüpunkte.
- Aktive Route klar sichtbar.

## Sprache und Labels
- Keine gemischten Deutsch/Englisch-Texte in einer Seite.
- Keine fehlerhaften Zeichen (Mojibake).
- CTA-Texte sind eindeutig.

## Formulare
- Pflichtfelder klar erkennbar.
- Fehlertexte direkt am Feld.
- Speichern/Abbrechen konsistent angeordnet.

## Feedback
- Jede Mutation zeigt Erfolg oder Fehler.
- Ladezustand sichtbar bei Netzwerkaktionen.
- Keine stillen Fehlschläge.

## Mobile
- Header, Navigation und Assistant überlagern sich nicht.
- Touch-Ziele ausreichend gross.
- Keine abgeschnittenen Tabellen ohne horizontales Scrollen.

## Accessibility Smoke
- Tastaturnavigation für Tabs, Buttons, Links.
- Fokus-Indikator sichtbar.
- Semantische Controls statt klickbare `div` ohne Rolle.
