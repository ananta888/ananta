# Blueprint Product Model (Standard Mode)

Dieses Dokument beschreibt das oeffentliche, nutzerorientierte Modell fuer Team-Start in Ananta.

## Public model

Die Standard-Sprache fuer Produktnutzer ist:

- **Role Template**: Wiederverwendbare Rollenanweisung (API bleibt `/templates`).
- **Blueprint**: Zusammenstellung aus Rollen, Role Templates, Starter-Artefakten und Governance-Hinweisen.
- **Team**: Laufende Instanz eines Blueprints fuer konkrete Ausfuehrung.

## Standard entry path

Der empfohlene Einstieg bleibt bewusst einfach:

1. Blueprint waehlen.
2. Team aus Blueprint instanziieren.
3. Arbeit starten und nur bei Bedarf in den Advanced-Modus wechseln.

Nutzer sollen nicht zuerst manuell Teams, Rollen und Templates zusammenbauen muessen.

## Advanced concepts

Die folgenden Begriffe bleiben erhalten, werden aber standardmaessig nicht in den Vordergrund gestellt:

- snapshot
- drift
- reconcile

Sie sind fuer Admin-/Studio- und Betriebsfaelle weiterhin verfuegbar.

