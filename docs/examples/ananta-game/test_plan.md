# Prüfung des lokalen Strategiespiels

## Automatisiert und headless

```bash
cd packages/ananta-game-core
npm ci
npm test
```

Die Node-Tests prüfen Karte und Symmetrie, Bewegung und AP, falsche Spieler und
veraltete Aktionen, Deva in beiden Aktionsreihenfolgen, Kampf einschließlich
Gleichstand, Rishi-Flucht samt Rücksetzung, Nagabanda am Rand und im Zentrum,
Naga-Einfluss, Terrain, Ashram-Schutz, Rekrutierung und Bereitschaft, Sieg und
Remis. Eingefrorene Eingaben prüfen unbeabsichtigte Mutation.

Replay-Tests enthalten erfolgreiche und abgelehnte Aktionen, Manipulationen,
Format- und Größenfehler sowie drei deterministische automatische Partien.
Diese kontrollieren Belegung, eindeutige Figuren, Ressourcen und Replay bis
zum Abschluss. Kein Test wartet auf Interaktion, KI-Dienste oder Freigaben.

```bash
cd frontend-angular
npm ci
npm run test:unit -- src/app/features/strategy-game/strategy-game.component.spec.ts src/app/components/strategy-game-demo.component.spec.ts src/app/app.routes.spec.ts
npm run build -- --configuration development
npx playwright install chromium
npm run test:strategy-game:browser
```

Komponententests bedienen echte Buttons: Auswahl, gültige Ziele, ungültige
Bewegung, Spielerwechsel, Rekrutierung, Sieg, Neustart und Replay-Import.
Route-Tests prüfen weiterhin die Einordnung unter der bestehenden Anmeldung.
Der isolierte Browser-Test lädt die echte Spielkomponente ohne Backend, spielt
bis zum Sieg, exportiert/importiert die Partie und prüft Tastaturbedienung,
Mobillayout und Barrierefreiheit. Die Auth-Route wird separat getestet; der
Browser-Test behauptet keine vollständige Hub-Integration.

## Spielbalance

Menschliche Spieltests sind optionales Produktfeedback, keine technische
Voraussetzung für Tests oder Automation. Die
[Protokollvorlage](playtest_protocol_template.md) unterstützt dieses Feedback.
Automatische Tests gelten nicht als Beleg für Spaß, Balance oder sinnvolle
Strategien. Besonders Startvorteil, Deva-Stärke und Rishi-Flucht benötigen
weitere Beurteilung. Regeln und Prototyp sind entsprechend als vorläufig markiert.
