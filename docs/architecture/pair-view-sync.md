# Pair Dev Compact App Sync

## Ziel

Compact App Sync teilt die Ananta-Angular-Oberflaeche ohne Bildschirmstream.
Es uebertraegt kleine, typisierte Zustandsdeltas und Mauspositionen innerhalb
einer bestehenden Pair-Session. Kamera, Mikrofon und Bildschirmfreigabe sind
getrennte Medienfunktionen und werden dadurch weder gestartet noch benoetigt.

## Sicherheits- und Datenfluss

```text
Angular Router / explizite Komponentenfelder
  -> SharedViewStateService (sanitisierte lokale Projektion)
  -> ViewDeltaService (vollstaendiger Snapshot oder minimales Delta)
  -> PairViewSyncService (Permission-Pruefung + E2EE)
  -> WebRTC DataChannel / erlaubter privater Hub-Relay
  -> authentifizierter Sender
  -> Validator
  -> separates RemoteViewProjection-Read-Model
```

Eingehender Zustand wird niemals in den lokalen `SharedViewStateService`
zurueckgeschrieben. Damit gibt es weder Echo-Schleifen noch implizite
Navigation. Ein Peer kann lokales Folgen oder Fernsteuerung nicht einschalten.
Solange keine explizite Freigabeoberflaeche fuer Remote Control existiert,
werden Steuerungsanfragen fail-closed abgelehnt.

Public Pair verwendet ausschliesslich den bestaetigten Strict-E2EE-Pfad der
konkreten Session/Sicherheitsepoche. Beim direkten WebRTC-DataChannel pruefen
beide Clients Payload-Typ, Traffic-Klasse, Einwilligung und Senderbindung; der
Rendezvous-Dienst sieht diese App-Nachrichten nicht. Beim optionalen privaten
Hub-Relay prueft der Server zusaetzlich die verschluesselten Metadaten,
Mitgliedschaft und Berechtigungen, aber nie den Klartext. Async-Ausgaben werden
an Session, Epoche und Sender gebunden; ein spaeter Abschluss aus einer alten
Session darf nicht in einen neuen Transport gelangen.

`pair.view_delta` und die dazugehoerige `pair.snapshot_request` duerfen in
beide Richtungen fliessen. Die Clients erzwingen diese Grenzen auf dem direkten
DataChannel. Wird der private Relay verwendet, akzeptiert auch er sie nur von
einem authentifizierten, aktiven Mitglied an ein anderes aktives Mitglied
derselben Strict-E2EE-Session mit aktueller Epoche, gueltiger Sequenz,
bestaetigter gegenseitiger Schluesselbindung, passender Traffic-Klasse und
`view_tui`.
Artefakt-, Steuerungs- und Cursor-Payloads behalten ihre jeweils getrennten
Berechtigungen.

## Kompakter Vertrag

`view_tui` erlaubt folgende begrenzte Felder:

- interner Pfad ohne Query-Parameter oder Fragment
- `activeSurface`, Tab und Panel
- numerische Viewport-Scrollposition sowie explizit angebundene kompakte
  Auswahl-, Zoom- und Einklappzustände
- keine Cursorposition: Maus-/Cursorwerte laufen ausschliesslich als eigener
  `pair.cursor`-Payload unter `remote_cursor` und lokaler Maus-Einwilligung

Nicht uebertragen werden DOM, Texteingaben/Formularwerte, URL-Parameter,
Browser-Tabs oder gerenderte Pixel. Artefakt-ID, Hash, Dateipfad und Symbol-ID
werden ohne zusaetzliches `artifact_share` vor Hash/Diff/E2EE auf `null`
redigiert. Artefaktinhalt ist nie Bestandteil dieses Protokolls.

Snapshots enthalten genau einmal jedes erlaubte Feld. Deltas akzeptieren nur
pfadspezifisch validierte Werte. Hashes ignorieren volatile Sequenz- und
Zeitfelder. Empfaenger halten pro authentifiziertem Sender eine eigene
Baseline; bei Luecken wird ein neuer Snapshot angefordert.

## Berechtigungen

| Backend-Key | Bedeutung | Schnellteilen |
|---|---|---:|
| `chat` | Pair-Chat | `true` |
| `view_tui` | kompakter Seitenzustand | `true` |
| `remote_cursor` | E2EE-Mausposition / Pair-Snake | `true` |
| `artifact_share` | Artefakt-Referenzen zusaetzlich erlauben | `false` |
| `remote_control` | explizite Fernsteuerung | `false` |

Der Button `Ananta-App schnell teilen` erstellt eine auf eine Stunde
begrenzte Public-Pair-Session mit genau diesen Werten. Er ruft weder
`getDisplayMedia` noch `getUserMedia` auf. Beim Ersteller merkt sich der Klick
ein einmaliges, nur im RAM gehaltenes Intent fuer diese Session. Erst die erste
bestaetigte Peer-Bindung aktiviert Ansicht und Maus fuer deren exakte Epoche;
ein spaeterer Rekey uebernimmt diese Einwilligung nicht. Das ausstehende Intent
kann bereits vorher widerrufen werden. Beitretende oder manuell erstellte
Sessions starten mit beiden lokalen Freigaben aus; jeder Teilnehmer kann
`Eigene Ansicht teilen` und `Eigene Maus teilen` unabhaengig aktivieren und
sofort wieder widerrufen.

Der UI-Einstieg bleibt in einer aktiven Session sichtbar. Fuer eine Session mit
beiden erforderlichen Rechten ist er ein lokaler Consent-Schalter. Andernfalls
erstellt er eine neue Quick-Session und parkt die vorherige Mitgliedschaft
serverautoritativ fuer das kanonische Konto/Geraet. Der Session-Katalog wird
OIDC-authentifiziert und mit den tablokalen Mitgliedschaftsnachweisen ueber den Rendezvous-Dienst
geladen; Keycloak liefert nur die Kontoidentitaet und niemals eine im Browser
auflistbare Benutzerverwaltung. Pro kanonischem Konto/Geraet ist genau eine
Session-Runtime aktiv. Ein Katalogwechsel parkt die vorige Runtime atomar,
schliesst ihren Transport ohne End-/Leave-Mutation, verwirft ihre alte
Transport-Epoche und verbindet die ausgewaehlte Session erst nach erneuter
Vertrags-, Geraete- und Epochenpruefung. Alle lokalen View-/Cursor-Consents
werden dabei zurueckgesetzt.

## Darstellung

`PairRemoteSnakeOverlayComponent` konsumiert nur authentifizierte
`peerCursors$`. Es hat absichtlich keine Hub-, AI-Snake-, Guide-, SSE-,
DOM-Snapshot- oder Raw-WebRTC-Abhaengigkeit. Jede Peer-ID bekommt
deterministisch eine Farbe; nur ein gekuerztes, nicht sensibles Hash-Label wird
angezeigt. Die Mauswerte werden beim Empfaenger auf dessen Viewport skaliert.

Die aktive Pair-Oberflaeche zeigt das `RemoteViewProjection` als reine
Seitenstatus-Anzeige. Es gibt keine automatische lokale Navigation.

## Datenbudget

Pointer-Ereignisse werden normalisiert, auf hoechstens 20 E2EE-Nachrichten pro
Sekunde begrenzt und nach dem Latest-Wins-Prinzip zusammengefasst. Seitenstatus
wird 80 ms entprellt und auf hoechstens 5 Deltas pro Sekunde begrenzt; langsame
Verschluesselung wird seriell abgearbeitet und behaelt nur den neuesten
ausstehenden Zustand. Ein echter Standardsnapshot bleibt im fokussierten
Vertragstest unter 8 KiB. Unabhaengig davon gilt fuer jede direkte eingehende
DataChannel-Nachricht ein harter 64-KiB-Pre-Crypto-Grenzwert.

## Komponenten

- `PairCompactAppSyncService`: idempotente Root-Aktivierung und Pointer-Capture
- `PairViewSessionBindingService`: bindet Share-Lifecycle an den Sync-Port
- `SharedViewStateService`: sanitisierte lokale Zustandsquelle
- `ViewDeltaService`: Snapshot/Delta-Erzeugung und pure Anwendung
- `PairViewSyncService`: E2EE-Senden, Validierung, Remote-Projektion, Cursor
- `PairRemoteSnakeOverlayComponent`: schlanker Public-Pair-Renderer

Die Hub-Worker-Architektur bleibt unveraendert: Der Hub autorisiert und
vermittelt; Worker erhalten weder UI-Zustand noch Maus- oder Mediendaten.
