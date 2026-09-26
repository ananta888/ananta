# Game-Core und Integrationsgrenze

Der unabhängige TypeScript-Kern liegt in `packages/ananta-game-core/`.
Die Angular-Oberfläche unter `/strategy-game` stellt diesen Kern dar und
übergibt Aktionen. Sie entscheidet keine Kampf-, Ressourcen- oder Zugregeln.
Die bestehende Architektur-Demo unter `/strategy-game-demo` bleibt separat.

## Zustände und Aktionen

`GameState` enthält Regelversion, Hexfelder, Figuren, Ashrams, Soma je Spieler,
aktiven Spieler, Runde, Zugnummer, AP, nächsten Figurenzähler und Sieger.
Figuren speichern Bewegung, Angriff, Flucht und Rekrutierungsruhe. Alles ist
JSON-serialisierbar; die TypeScript-Schnittstellen sind schreibgeschützt.

`createInitialState()` erzeugt die feste Startstellung. `validateAction(state,
action)` liefert eine gültige, geschlossene Aktion oder einen stabilen Fehlercode.
`applyAction(state, action)` validiert selbst erneut und liefert einen neuen
Zustand bzw. bei Ablehnung denselben unveränderten Zustand. Es gibt keine Uhr,
Zufallsquelle, Datenbank, Netzwerkverbindung oder globale veränderbare Partie.

Jede Aktion hat `player` und `turn`. Veraltete Züge, falsche Spieler und
unbekannte Felder im Aktionsformat werden zurückgewiesen.

| Aktion | Nutzdaten | Wirkung |
| --- | --- | --- |
| MOVE | unitId, to | Nachbarfeld betreten, 1 AP |
| ATTACK | unitId, targetId | Deterministischer Kampf / Rishi-Flucht, 2 AP |
| RECRUIT | kind, to | Kosten bezahlen und ruhende Figur am Ashram erzeugen |
| END_TURN | keine | Wechsel, AP-Reset, eigenes Einkommen und Figuren-Reset; ggf. Remis |
| NAGABANDA_CHECK | unitId | Abfrage ohne Zustandsänderung oder AP-Kosten |

Die pure API akzeptiert vertrauenswürdige, vom Core erzeugte Zustände; sie ist
keine Validierungsgrenze für beliebige fremde Spielstände. Replay-Import nimmt
deshalb ausschließlich Aktionen gegen die kanonische Startstellung an.

## Verantwortlichkeiten und SOLID-Prüfung

- `model` / `board`: Datentypen, Regelwerte und Hexgeometrie.
- `actions` / `validateAction`: geschlossenes Aktionsformat und Zulässigkeit.
- `nagabanda` / `combat`: Einkreisung, Flucht und Kampfausgang.
- `applyAction`: validierte Zustandsübergänge.
- `replay`: Sitzungen und begrenzter, versionierter Import/Export.
- Angular-Komponente: Auswahl, Darstellung und lokale Bedienung.

SRP trennt Spielregeln, Darstellung und Replay. DIP: Der Core importiert kein
Frontend oder Backend. Es gibt keine Vererbung, austauschbaren Implementierungen
mit abweichenden Verträgen, breiten Service-Interfaces oder versteckten IO-Effekte.
Bestehende Architektur-Demo und Python-Game-Modelle werden nicht umgedeutet.

Bewusste OCP-Grenze: Aktionsparser, Validator und Reducer besitzen geschlossene
`switch`-/Typverzweigungen. Ein neuer Aktionstyp erfordert koordinierte Änderungen
und eine explizite Regelversion. Für den kleinen, festen Regelsatz ist dies
übersichtlicher als ein Plugin-System; spätere Economy-/Arena-Regeln sollten
über versionierte Regelstrategien integriert werden, sobald dafür Bedarf besteht.

## Ananta und Container

Das lokale Spiel führt keine Ananta-Aufträge oder Agenten aus. Es benötigt keine
zusätzliche Orchestrierung. Bei einer späteren KI-Anbindung bleiben Aufträge,
Delegation und Policy beim Hub; Worker dürfen ausschließlich Vorschläge liefern.
Die Angular-Route bleibt unter der bestehenden Anmeldung.

Frontend-Images müssen den Core-Quellcode relativ zum Frontend mitliefern:
`frontend-angular/Dockerfile` verwendet `/packages/ananta-game-core/src`, das
Quickstart-Image kopiert das vollständige Repository nach `/app`.
Entwicklungs-Compose mountet den Core schreibgeschützt für Live-Änderungen.
Der Core hat keine Runtime-Abhängigkeiten und kann separat gebaut werden.

Tests und lokal exportierte Partien sind technische Beobachtungen bzw.
synthetische Spieldaten. Sie sind keine Hub-registrierte Release-Evidenz.
