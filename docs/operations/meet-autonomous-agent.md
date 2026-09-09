# Autonomer Meet-Agent: aktueller Betriebsweg

Dieser Einstieg beschreibt die implementierten Betriebsbausteine, nicht eine
bereits erteilte öffentliche Freigabe. Der frühe
[GPU-Leitfaden](meet-local-gpu-agent.md) dokumentiert die erste Ausbaustufe;
Dialog, Persona-Auswahl, eigener Browserstream und getrennte Worker sind
inzwischen implementiert und besitzen separate private Referenzprüfungen.
Maßgeblich für offene Abnahmen bleibt
[`todo.meet-autonomous-agent-persona-media.json`](../../todos/todo.meet-autonomous-agent-persona-media.json).

## Drei unabhängige Voraussetzungen

| Grenze | Verantwortlicher und erforderliche Konfiguration |
| --- | --- |
| Laufende Software | Beide Repository-/Buildstände und unveränderliche Worker-Image-ID festhalten. Ein aktueller Checkout aktualisiert weder Serving-Bundle noch laufenden Container. |
| Meet-Maschinenaufnahme | Der Meet-Betreiber installiert ausschließlich den öffentlichen Hub-Trust: exakter Issuer, Audience, Key-ID/Zeitfenster, Principal-/Tenant-/Projekt-/Capability-Tupel und globales Ceiling. Human-OIDC und SFrame bleiben aktiv. |
| Hub-Ausführungsrecht | Der Hub prüft Projekt-/Task-/Organisationsrechte, Raumzuordnung, Rollen, Kapazität und aktuelle Vorautorisierung. Ein Meet-Grant ersetzt keine davon und keine Publisher-Empfangsfreigabe. |

Der [Readiness-Check](../contracts/meet-live-readiness.md) liest nur geschlossene
Health-/Capability-/Versionswerte. `observed` bedeutet nicht, dass ein bestimmter
Hub-Key oder ein Projekt zugelassen ist. Das Gegenstück im Meet-Repository sind
`docs/machine-production-compose.md` und `docs/machine-trust-preflight.md` mit
dem dortigen Deployment-Selektor und versionierten, rein lesenden Preflight.
Kein grüner lokaler Test aktiviert diese Grenzen automatisch.

## Konfiguration und unbeaufsichtigter Start

Der Hub verwendet `docker-compose.meet-media-hub.yml` zusätzlich zu seiner
normalen Compose-Konfiguration; Worker/Ollama laufen im getrennten Projekt aus
`docker-compose.meet-media.yml`. Die bestehenden Netze werden ergänzt, nicht
ersetzt. Private Hub-Schlüssel werden niemals in den Worker oder Meet gemountet.
Worker-Rückrufadressen bleiben die explizit konfigurierten privaten Hub-Ports.

Die relevanten Hub-Optionen sind:

- `ANANTA_MEET_ENABLED`, `ANANTA_MEET_MEDIA_ENABLED`,
  `ANANTA_MEET_MACHINE_ENABLED`, `ANANTA_MEET_DIALOG_ENABLED`: getrennte Opt-ins;
  Publish-Scopes und `ANANTA_MEET_DIALOG_POLICIES` müssen exakt konfiguriert sein.
- `ANANTA_MEET_MACHINE_KEY_ID`: optionaler geschützter JWT-Key-Pin; kein
  caller- oder Worker-selektierter Schlüssel.
- `ANANTA_MEET_ROOM_ALLOCATION_ENABLED` und `_SCOPES`: eigene, begrenzte
  [headless Raumzuordnung](../contracts/meet-headless-room-assignment.md).
- `ANANTA_MEET_DIALOG_PREAUTHORIZATION_ENABLED`: zusätzliche Task-/Raumgrenze
  gemäß [automatisierter Provisionierung](meet-dialog-preauthorization.md).
- `ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED` und optional
  `ANANTA_MEET_DIALOG_WORKER_URLS`: [Rollen-/Worker-Zuordnung](../contracts/meet-multi-agent-isolation.md).
  Die URL-Option **nicht setzen** für die bisherige Single-Worker-Komposition;
  selbst `[]` ist ein explizites Opt-in in den Selektor. Keine Rollenrechte
  entstehen allein durch einen URL-Eintrag.
- `ANANTA_MEET_DIALOG_CAPACITY` und `_POOL`: bestehende SQL-Kapazitätsgrenze.
  Leeres Objekt verwendet das vorhandene Profil, keine unbegrenzte Kapazität.
- `ANANTA_MEET_SPEAKER_FLOOR` und `_POLICIES`,
  `ANANTA_MEET_DIALOG_RECONNECT`, `ANANTA_MEET_MEDIA_TIMING`: separate,
  standardmäßig ausgeschaltete Sprech-, Recovery- und Qualitätsprofile.

Das [Hub-Overlay](../contracts/meet-hub-compose-options.md) reicht diese Werte
weiter; es erzeugt keine Policy. Compose-Mergepriorität gegenüber einer älteren
Basis-/Service-env-Datei vor einem Deployment prüfen. Die neuen
strikten Felder behalten ausdrücklich leere/ungültige Werte für die vorhandene
Hub-Validierung, statt sie still durch eine Freigabe zu ersetzen. Alle neuen
Feature-Standards bleiben aus. Auf dem Worker muss zusätzlich
`MEET_DIALOG_ENABLED=1` explizit gesetzt werden. Persona-Kataloge, zulässige
Bild-/Video-/Stimmrevisionen und Modellbytes bleiben eigenständige Voraussetzungen.

Nach der einmaligen Betreiberkonfiguration können autorisierte API-Principals
Starts, Quellsteuerung und Stop vollständig automatisch ausführen. Der
[Dialogvertrag](../contracts/meet-dialog-runtime.md) beschreibt die bestehenden
`/api/meet/v1/projects/<project>/…`-Endpunkte. Taskgebundene Starts verwenden
die zugehörige `/tasks/<task>/dialogs`-Route. Bei ungewissem Startausgang die
[Idempotenzbindung](../contracts/meet-dialog-start-idempotency.md) und den
vorhandenen Statuspfad verwenden; kein blindes erneutes Dispatch.

Ein Testkonto für den öffentlichen Human-OIDC-Gegenpart ist keine manuelle
Freigabe im Test: Der Test meldet sein eigenes Konto automatisch an und setzt
seine eigene Publisherpolicy per UI/API. MFA, fehlende Zulassung oder fehlende
Secrets beenden ihn begrenzt; sie öffnen keinen interaktiven Wartezustand.
Testidentitäten dürfen keine fremden Geräte, Browserprofile oder Meetings nutzen.
Ohne ein vorgesehenes Konto wird weder OIDC abgeschaltet noch ein Maschinenpeer
als berechtigter menschlicher Publisher ausgegeben.

## Stufenweiser Funktionsnachweis

Die Stufen sind ein Prüfablauf, keine neuen API-Modi. Je Stufe werden nur die
notwendigen vorhandenen Capabilities ausgegeben und Quellen explizit aktiviert.

| Stufe | Bestehender Pfad und erforderliche Beobachtung |
| --- | --- |
| Stumm/eigener Browser | Begrenzter Dialog ohne Chat-/Audioempfang; ausschließlich task-eigene Bildschirmquelle. Tatsächlich bewegte Empfängerpixel und Stop prüfen, nicht nur Join-200. |
| Bild und TTS | Admittiertes Persona-Bild, ausdrücklich aktivierte Sprachausgabe und exakt gebundener Profil-/Modellstand. Tatsächlich empfangene nichtstille PCM-Ausgabe prüfen. |
| Eigenes Persona-Video | Separat ausgehandeltes `avatar_videos` mit explizitem `loop`/`hold_last`; Bild-, Video- und Sprachgeneration bleiben unabhängig. Keine semantische Lippensynchronität behaupten. |
| Receive und Dialog | Separate Chat-/Audio-Capabilities, Hub-Modus und aktuelle Freigabe jedes Publishers. Korrelierte Antwort, Dubletten-/Self-Echo-Abweisung und Widerruf testen. |
| Mehragentenbetrieb | Eindeutige aktuelle Rollen-/Worker-Zuordnung je Task, separate Container/Sitzungen und Hub-Kapazität; eine Quelle widerrufen, während die andere weiterarbeitet. |

Die [initiale Persona-Auswahl](../contracts/meet-dialog-initial-persona.md)
bindet auf Wunsch Bild/Video/Stimme vor Dispatch. Spätere autorisierte CAS-
Änderungen ersetzen nicht die historische Startbindung. Eine entzogene oder
fehlende Revision führt nicht zu einer heimlich gewählten Ersatzpersona.
Das [Qualitätsprofil](../contracts/meet-reference-quality-profile.md) hält
vorab festgelegte Drift-, Frische-, Ressourcen- und Stopgrenzen fest. Neue
Messungen dürfen diese Grenzen nicht nachträglich zum Bestehen erhöhen.

## Not-Aus, Widerruf und Rückweg

1. Einzelne Quellen mit dem vorhandenen `PATCH` und exakter
   `expected_revision` pausieren; ganze eigene Tasks per bodylosem `DELETE`
   auf `/projects/<project>/dialogs/<task_id>` stoppen. Ein abgelaufenes
   Ausführungsrecht darf den berechtigten Stop nicht blockieren. Das Schließen
   des Angular-Panels ist **kein** Task-Stop.
2. Bei Policy-/Projekt-/Rollentzug die vorhandene Hub-Autorität widerrufen.
   Der normale Worker-Frischewächter schließt betroffene Ausgaben. Alte
   Generationen und verspätete Modellantworten dürfen nicht wieder erscheinen.
   Eine vergebene Meet-Lease ist dennoch keine sofort fernwiderrufene
   Berechtigung eines kompromittierten Workers; ihre eigene Ablaufgrenze gilt.
3. Erst eigene Tasks kontrolliert stoppen, dann einen geplanten Deployment-
   oder Trustwechsel ausführen. Das Abschalten einer Bootstrap-Variable ist
   kein Hot-Reload und kein belegter sofortiger Stop laufender Tasks.
4. Meet-Trustentzug benötigt die explizite gültige Deployment-Auswahl und
   einen kontrollierten Neustart. Alle Räume dieser Instanz sind flüchtig;
   deshalb ist dieser Weg nicht der harmlose Not-Aus eines einzelnen Agents.
   Bestehende Teilnehmer/Betreiberkoordination vorher berücksichtigen.

Für geplante Schlüsselrotation zunächst mit dem
[Key-Provisionierer](../contracts/meet-machine-key-provisioning.md) einen neuen
privaten Hub-Key in einem eigenen Verzeichnis vorbereiten; bestehende Dateien
werden nicht überschrieben. Öffentliche alte/neue Schlüssel mit überlappenden
Gültigkeitsfenstern im expliziten [Meet-Trustprofil](../contracts/meet-machine-trust-profile.md)
zulassen. Das gewählte Fenster muss die vollständige Grantlaufzeit abdecken.
Anschließend den Hub auf neuen Key und `ANANTA_MEET_MACHINE_KEY_ID` umstellen.
Entzug des alten Keys ist ein eigener Trust-/Neustartschritt, kein Resultat
eines Image-Rollbacks. Keine privaten Keys in Git, Browser oder Logs kopieren.

Ein Software-Rollback verwendet nur explizit kompatible, festgehaltene Images
und Quellen. Er setzt weder SQL-Policyrevisionen noch Meet-Trust, Persona-
Auswahl oder verbrauchte Dispatchbudgets zurück. Eine alte Policyrevision darf
nicht reaktiviert werden, um alte Tasks wieder gültig zu machen. Falls der
alte Softwarestand den aktuellen geschlossenen Vertrag nicht unterstützt,
bleibt der Maschinenpfad aus, statt Validierung oder SFrame zu umgehen.

## Nachweise, Datenschutz und aktuelle Grenze

Der [Hub-Test-Runner](../contracts/meet-test-evidence-runner.md) reserviert
Registry-Identitäten **vor** dem ausgewählten Lauf und prüft anschließend
unveränderte Quellen, Frontendbytes, Treiber und Images. Ein erfolgreicher
freier Shell-Aufruf bleibt eine technische Beobachtung. Synthetische Policy-
und Testläufe erfüllen niemals ein Produktions-Release-Gate.

Logs enthalten nur geschlossene Codes, Zähler und freigegebene Messwerte.
Keine Tokens, OIDC-Seiteninhalte, SDP/ICE, Chattexte, PCM oder Bilder persistieren.
Die [optionale Abschlussdiagnose](../contracts/meet-dialog-terminal-diagnostics.md)
und [Phasenansicht](../contracts/meet-dialog-phase-ui.md) sind von Tasksteuerung
getrennt: Historische Frische ist weder aktuelle Medienlieferung noch Release-
Evidenz. Fehlende Messwerte bleiben unbekannt.

Am 9. September 2026 wurde die öffentliche Instanz rein lesend als erreichbar,
mit erforderlichem Human-Auth/SFrame und konfiguriertem TURN beobachtet;
Maschinenaufnahme war weiterhin aus. Der laufende ältere Image-/Quellstand
und der aktuelle Checkout sind in der Readiness-/TODO-Historie getrennt.
Öffentlicher Maschinenlauf, ausgewähltes TURN-Paar mit Nutzdaten, zweistündige
öffentliche Abnahme und Rollout bleiben offen. Ein lokaler Zweibrowser-Hairpin
belegt keine unabhängige externe NAT-/Multi-Host-Gegenstelle.

SRP/DIP: Dieser Index verwendet vorhandene Hub-Policy-, Task-, Transport- und
Meet-Deployment-Ports; er schafft keinen zweiten Orchestrator. Die großen
bestehenden Hub-Composition-/Integrationsfixtures bleiben benannte SRP-Schulden.
Die Compose-Ergänzung transportiert Konfiguration, trifft aber keine neue
Autorisierungs- oder Worker-Auswahlentscheidung.
