# Blueprint Product Model (Standard Mode)

Dieses Dokument ist der kurze Produktleitfaden fuer normale Nutzer. Es erklaert nur die drei Kernbegriffe und den empfohlenen Startweg.

## Public model

Die Standard-Sprache fuer Produktnutzer ist:

- **Role Template**: Wiederverwendbare Rollenanweisung (API bleibt `/templates`).
- **Blueprint**: Zusammenstellung aus Rollen, Role Templates, Starter-Artefakten und Governance-Hinweisen.
- **Team**: Laufende Instanz eines Blueprints fuer konkrete Ausfuehrung.

## Was nutze ich wann?

- **Blueprint**: Wenn du strukturiert starten willst und eine klare Standardvorgehensweise brauchst.
- **Role Template**: Wenn du Rollenanweisungen wiederverwendbar machen oder gezielt aendern willst.
- **Team**: Wenn die eigentliche Arbeit laeuft und Aufgaben ausgefuehrt werden.

Merksatz: **Blueprint waehlen -> Team starten -> Arbeit ausfuehren**.

## Standard entry path

Der empfohlene Einstieg bleibt bewusst einfach:

1. Gefuehrte Auswahl nutzen (Zieltyp, Striktheit, Domaene, Ausfuehrungsstil).
2. Blueprint passend zum Ziel waehlen (mit begruendeter Empfehlung).
3. Vorschau der Start-Rollen und Start-Aufgaben pruefen.
4. Team aus Blueprint instanziieren.
5. Arbeit starten und nur bei Bedarf in den Advanced-Modus wechseln.

Nutzer sollen nicht zuerst manuell Teams, Rollen und Templates zusammenbauen muessen.

## Gefuehrte Auswahl und Empfehlung

Die gefuehrte Auswahl liefert fuer Erststart und Wizard-Flows eine erklaerbare Empfehlung:

- Eingaben: Zieltyp, Striktheit, Domaene, Ausfuehrungsstil
- Ausgabe: empfohlener Standard-Blueprint, Work-Profile-Hinweise und Instanziierungsvorschlag
- Review: Die Begruendung bleibt vor Team-Start sichtbar und pruefbar

## Standard mode vs. admin/studio mode

- **Standard mode**: Fokus auf Blueprint-Auswahl, Team-Start und klare Ergebniserwartung.
- **Admin/studio mode**: Detailpflege fuer Blueprint-Struktur, Role Templates, Lifecycle- und Migrationsdetails.

Damit bleibt der Erstnutzerfluss kompakt, ohne operative Tiefe zu verlieren.

## Advanced concepts

Die folgenden Begriffe bleiben erhalten, werden aber standardmaessig nicht in den Vordergrund gestellt:

- snapshot
- drift
- reconcile

Sie sind fuer Admin-/Studio- und Betriebsfaelle weiterhin verfuegbar.

Im Standardmodus werden stattdessen vereinfachte Team-Zustaende gezeigt:

- `Standard`
- `Angepasst`
- `Aktualisierbar`

Diese Begriffe werden auch fuer Seed-/Default-Blueprints in der normalen Produktsicht verwendet.

## Dokumente nach Zielgruppe

**Standardnutzer (Produktweg):**

- `docs/blueprint-product-model.md` (dieses Dokument)
- `docs/standard-blueprints.md`

**Admin/Studio und Rollout:**

- `docs/blueprint-admin.md`
- `docs/blueprint-migration-rollout.md`
- `docs/blueprint-bundle-import-export.md`
- `docs/blueprint-studio-roadmap.md`
