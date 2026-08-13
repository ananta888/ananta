# Pair Dev: Audio und Video

Pair Dev verwendet fuer normale Zwei-Personen-Gespraeche den direkten
WebRTC-Pfad. Eine private Session wird vom Hub autorisiert. Bei einer
oeffentlichen v2-Session autorisiert die bestaetigte Public-Pair-Bindung den
Medienpfad; ein lokaler Hub ist dafuer nicht erforderlich. Audio- und
Videodaten werden nicht als Worker-Tasks verarbeitet.

## Voraussetzungen

- Beide Browser sind im Pair-Dev-Bereich angemeldet. Bei Public Pair duerfen
  sie denselben Keycloak-Account verwenden, muessen dann aber unterschiedliche
  lokale P-256-Geraeteschluessel besitzen und eine neu erstellte v2-Session
  verwenden.
- Beide Browser sind derselben aktiven, nicht widerrufenen Share-Session
  beigetreten.
- Das aktive Netzwerkprofil verwendet den Transport `webrtc`; ein
  `hub_relay`-Fallback uebertraegt keine Audio- oder Videospuren.
- Keycloak, Signaling sowie die konfigurierten STUN-/TURN-Dienste sind vom
  jeweiligen Browser erreichbar.
- Die Seite laeuft ueber HTTPS oder auf `localhost`, damit der Browser
  Mikrofon und Kamera freigeben darf.

Zusaetzlich gilt fuer **Public Pair**:

- Die Sicherheitsanzeige muss die gegenseitige Geraete- und
  Schluesselbestaetigung abgeschlossen haben.
- Beide Browser muessen den von Ananta verwendeten WebRTC-Encoded-Transform
  unterstuetzen. Fehlt diese Browserfunktion, bleibt Capture geschlossen.
- Der separate signierte `public_media_security_contract_v2` muss die
  Medien-Grants, Publikations-Slots und den E2EE-Transform fuer beide Peers
  bestaetigen. Der bestehende Public-Pair-Basisvertrag bleibt unveraendert auf
  `bulk`, `control` und `semantic` begrenzt. Die Anwendung akzeptiert keine
  lokale oder ungesicherte Ersatzfreigabe.
- Bereits vor dieser Medienversion erstellte Public-Pair-Sessions bleiben
  absichtlich reine Daten-Sessions. Nach dem Client-Update eine neue Session
  erstellen und mit dem aktualisierten zweiten Browser erneut beitreten.
- Das Hub-Flag `ANANTA_ORDINARY_MEDIA_PUBLICATION_ENABLED` wird fuer Public
  Pair weder benoetigt noch als Autoritaet ausgewertet.

Fuer eine **private Hub-Session** muss der Hub dagegen mit
`ANANTA_ORDINARY_MEDIA_PUBLICATION_ENABLED=true` gestartet sein. Die
weitergehenden Flags fuer semantische Bildaufnahme, Speech-Runtime und SFU
sind fuer einen direkten Zweier-Call nicht erforderlich und bleiben
standardmaessig deaktiviert.

## Medienverschluesselung bei Public Pair

Vor dem ersten Browser-Capture richtet Ananta fuer die konkrete Session und
den bestaetigten Gegenueber-Schluessel einen Medien-E2EE-Pfad ein. Audio- und
Video-Frames werden im sendenden Browser verschluesselt und erst im
empfangenden Browser wieder entschluesselt. Rendezvous, STUN und TURN erhalten
keinen Medien-Inhaltsschluessel. Kann der Pfad nicht vollstaendig eingerichtet
werden oder geht die Sicherheitsbindung verloren, bleiben neue Freigaben
gesperrt und laufende Public-Pair-Medien werden beendet. Damit Chromium und
Firefox die VP8-/Opus-Frames weiterhin korrekt paketieren, bleiben nur der
VP8-Uncompressed-Prefix (10 Byte bei Keyframes, 3 Byte bei Deltaframes) bzw.
das einzelne Opus-TOC-Byte sichtbar. Diese Bytes sind Bestandteil der
AES-GCM-Authentifizierung; der restliche Medienframe bleibt verschluesselt.

## Ablauf in der Oberflaeche

1. Im Hauptmenue `Public Pair Dev` oeffnen (Route `/pair-dev`). Dadurch wird
   der kompakte Pair-Dev-Modus im AI-Snake-Fenster rechts unten geoeffnet.
   Der Tab `Pair Dev` zeigt Session, Pair-Chat und Teilnehmer. Dort mit
   Keycloak anmelden.
2. Eine Share-Session erstellen oder ihr beitreten und warten, bis die
   WebRTC-Verbindung steht.

Fuer eine besonders schnelle Zusammenarbeit kann der Owner stattdessen
`Ananta-App schnell teilen` waehlen. Dieser eine Klick erstellt eine auf eine
Stunde begrenzte E2EE-Session fuer Pair-Chat, internen Ananta-Seitenpfad,
Bereich, numerische Scrollposition und Mauspositionen. Die Maus jedes
Gegenuebers erscheint als farbige Pair-Snake.
Dabei werden keine Bildschirmpixel, Kamera, Mikrofon, Formularwerte oder
URL-Parameter uebertragen. Der Peer-Seitenstatus ist eine reine Anzeige;
Ananta wechselt nicht automatisch die lokale Seite und erlaubt dadurch keine
Fernsteuerung. Die Medienfunktionen im Tab `Medien` bleiben davon unabhaengig.
Der Schnellteilen-Klick merkt beim Ersteller einmalig die lokale Freigabe fuer
diese Session vor. Sie wird erst bei der ersten bestaetigten Peer-Bindung fuer
deren exakte Sicherheitsepoche aktiv und kann bereits vorher mit `Ausstehende
Schnellfreigabe widerrufen` zurueckgenommen werden. Ein spaeterer Rekey
uebernimmt sie nicht. Beitretende Personen entscheiden unter `Meine kompakte
Freigabe` separat mit `Eigene Ansicht teilen` und `Eigene Maus teilen`; beide
Schalter lassen sich ohne Neuladen sofort widerrufen und gelten nur fuer die
aktuelle Session und Sicherheitsepoche.

Der Schnellteilen-Schalter bleibt auch waehrend einer laufenden Session oben
im Pair-Bereich erreichbar. Besitzt die aktive Session bereits die Rechte
`view_tui` und `remote_cursor`, schaltet er die eigene kompakte Ansicht und
Maus fuer genau diese Session ein oder aus. Fehlen diese unveraenderlichen
Sessionrechte, lautet die Aktion `Neue Schnellteilen-Session`: Die bisherige
Mitgliedschaft wird fuer dieses Keycloak-Konto und Geraet serverseitig geparkt
und eine passende neue Session erstellt. Sie wird dabei weder beendet noch
verlassen; ihre bisherige Transport-Epoche wird ungueltig.

Unter `Sessions verwalten` listet Ananta die im aktuellen Browser-Tab sicher
wiederverbindbaren eigenen und beigetretenen Pair-Sessions. `Chat oeffnen`
kehrt bei der bereits aktiven Session zu Chat und Details zurueck; nur der
Eigentuemer sieht dort den Einladungscode. `Wechseln` haelt
fuer dasselbe Konto und Geraet immer nur eine WebRTC-/E2EE-Verbindung aktiv;
alle anderen Eintraege bleiben serverseitig geparkt. Beim Wechsel werden
Kamera, Bildschirm, Mikrofon sowie lokale
Ansicht-/Maus-Einwilligungen nicht auf das neue Gegenueber uebertragen. Sie
muessen dort erneut bewusst aktiviert werden. `Session beenden` und
`Verlassen` bleiben davon getrennte, ausdruecklich destruktive Aktionen.

Keycloak bestaetigt beim Laden der Liste das angemeldete Konto. Die eigentliche
Mitgliedschaftssuche, Presence, Geraetebindung, Signalisierung und beidseitige
Schluesselbestaetigung fuehrt `webrtc.ananta.de` aus. Keycloak selbst ist kein
oeffentliches Teilnehmerverzeichnis. Eine neue Person wird weiterhin mit dem
Einladungscode autorisiert; danach erscheint die gemeinsame Session bei beiden
angemeldeten Konten in deren Liste. Unterschiedliche Gegenueber verwenden
getrennte bilaterale Sessions.

3. In der unteren Leiste `Medien` oeffnen. Bei **Public Pair** unter
   `Meine Medienfreigabe` die Gueltigkeit waehlen:
   `Bis zum Ende dieser Pair-Sitzung oder bis zum Widerruf`, `15 Minuten` oder
   `1 Stunde`. Danach `Einwilligen und Medien aktivieren` auswaehlen. Diese
   Einwilligung gilt ausschliesslich fuer die Uebertragung des eigenen
   Mikrofons, der eigenen Kamera und des eigenen Bildschirms. Beide Personen
   entscheiden unabhaengig voneinander; der Empfang der Medien des
   Gegenuebers wird dadurch nicht ausgeschaltet. Bei einer privaten
   Hub-Session bleibt stattdessen die bestehende Hub-Freigabe massgeblich.
4. `Mikrofon freigeben`, `Kamera freigeben` oder `Bildschirm freigeben`
   bewusst anklicken und die Browserabfrage bestaetigen.
5. Empfangene Audiospuren erscheinen im Remote-Audio-Player. Empfangene
   Kameras und Bildschirmfreigaben werden als eigene Remote-Videos angezeigt.

`Bildschirm freigeben` uebertraegt aktuell nur das Bild. Fuer Ton parallel
`Mikrofon freigeben` verwenden; Browser- oder Systemton der Bildschirmquelle
wird noch nicht mitgesendet.

Das Oeffnen des Pair-Dev-Medienbereichs und auch
`Einwilligen und Medien aktivieren` fordern noch keine Browserrechte an und
starten keine Aufnahme. Erst der separate Klick auf eine konkrete Quelle
oeffnet den Browserdialog.
Stummschalten, Stoppen und Wechseln einer lokalen Quelle bleiben explizite
Benutzeraktionen. `Eigene Freigabe deaktivieren` und das zeitliche Ablaufen
der Einwilligung beenden nur die eigenen lokalen Publikationen. Die
WebRTC-Verbindung, der Chat und empfangene Medien des Gegenuebers bleiben
verbunden. Dieselbe Person kann ihre Freigabe danach in derselben
Pair-Sitzung erneut aktivieren; eine Neuanmeldung oder ein Neuladen ist dafuer
nicht erforderlich.

Das AI-Snake-Fenster darf mit `v` minimiert oder mit `x` ausgeblendet werden.
Die eingebettete Pair-Session und bereits freigegebene Medien laufen dabei
weiter; ueber den `AI Snake`-Starter wird dieselbe laufende Oberflaeche wieder
eingeblendet. `Session beenden`, `Verlassen` und `Eigene Freigabe
deaktivieren` bleiben dagegen ausdrueckliche Beendigungsaktionen.
Auch ein normaler Wechsel auf eine andere Ananta-Seite beendet die aktive
Pair-Session nicht. Der Pair-Owner bleibt im App-Shell erhalten, damit Chat,
kompakter App-Sync und bereits freigegebene Medien weiterlaufen. Erst
`Session beenden` beziehungsweise `Verlassen` beendet die Teilnahme.

Die Auswahl `Bis zum Ende dieser Pair-Sitzung oder bis zum Widerruf` ist keine
kontoweite oder geraeteuebergreifende Dauereinwilligung. Sie ist an den aktuellen Browser-Tab,
die exakte Pair-Session, deren Sicherheitsepoche, den signierten Medienvertrag
und das konkrete Gegenueber gebunden. Ein anderer Tab, eine neue Session, ein
gewechseltes Gegenueber oder ein neuer Medienvertrag uebernehmen die
Einwilligung nicht. Eine zeitlich begrenzte Freigabe endet spaetestens mit der
Pair-Session bzw. mit dem Medienvertrag.

## Fehler einordnen

| Anzeige oder Code | Bedeutung |
|---|---|
| `popup_blocked` | Der Browser blockiert das Keycloak-Popup. Pop-ups fuer die Ananta-Seite erlauben und erneut klicken. |
| `issuer_unreachable` | Der OIDC-Issuer ist nicht erreichbar oder liefert keine gueltige Discovery-Konfiguration. |
| `ordinary_media_publication_disabled` | Das Hub-Feature-Flag ist nicht aktiv. Hub mit dem obigen Flag neu erstellen. |
| `ordinary_media_webrtc_transport_required` | Die Session verwendet keinen direkten WebRTC-Medienpfad. Signaling/STUN/TURN pruefen. |
| `ordinary_media_session_missing` | Es besteht keine aktive Share-Session. |
| `public_ordinary_media_e2ee_not_ready` | Die bestaetigte Pair-Sicherheitsbindung oder der Medien-E2EE-Pfad ist noch nicht bereit. |
| `public_ordinary_media_e2ee_awaiting_security` | Die gegenseitige Pair-Schluesselbestaetigung ist noch nicht abgeschlossen. |
| `public_ordinary_media_e2ee_awaiting_peer` | Die Browser handeln den technischen Medien-E2EE-Pfad noch aus. Dies ist keine fehlende Publikationseinwilligung des Gegenuebers. |
| `public_ordinary_media_e2ee_negotiating` | Die Browser richten die schluesselgebundenen Medientransforms ein; dabei startet noch keine Aufnahme. |
| `public_ordinary_media_e2ee_unavailable` | Der sichere Public-Medienpfad ist in diesem Browser nicht verfuegbar. |
| `media_e2ee_transform_unsupported` | Der Browser stellt keinen unterstuetzten WebRTC-Encoded-Transform bereit. |
| `public_media_runtime_unsupported` | Der Browser unterstuetzt den standardisierten `RTCRtpScriptTransform`-Pfad nicht vollstaendig. |
| `public_media_contract_not_ready` | Der separate signierte Medienvertrag fehlt noch; Sicherheitsbestaetigung abwarten oder mit zwei aktualisierten Browsern eine neue Session erstellen. |
| `public_media_contract_missing` | Fuer diese Session wurde kein separater signierter Medienvertrag ausgehandelt; mit zwei aktualisierten Browsern eine neue Session erstellen. |
| `public_media_contract_expired` | Der Public-Medienvertrag ist abgelaufen; eine neue Pair-Session erstellen. |
| `public_media_transform_not_prepared` | Der WebRTC-Pfad bzw. seine festen E2EE-Medienslots sind noch nicht vorbereitet. |
| `public_media_peer_consent_pending` | Ein aelterer Client wartet noch auf die technische Medien-Aushandlung. Beide Browser aktualisieren; eine separate Empfangseinwilligung ist nicht erforderlich. |
| `public_media_peer_activation_pending` | Ein aelterer Client wartet noch auf die technische Medien-Aushandlung. Beide Browser aktualisieren; die eigene Publikation bleibt separat steuerbar. |
| `public_media_publication_consent_required` | Die lokale Uebertragung ist nicht freigegeben. Unter `Meine Medienfreigabe` eine Dauer waehlen und einwilligen. |
| `public_media_publication_consent_expired` | Die lokale Einwilligung ist abgelaufen. Sie kann in derselben Pair-Session erneut erteilt werden. |
| `public_media_transport_closed` | Die direkte WebRTC-Verbindung wurde geschlossen. Fuer Public-Medien eine neue Pair-Session erstellen und auf beiden Seiten erneut aktivieren. |
| `public_media_fresh_connection_required` | Ein ICE-/Netzwerk-Neustart darf die alten Medienschluessel nicht wiederverwenden. Eine neue Pair-Session erstellen. |
| `media_e2ee_transform_install_failed` | Der schluesselgebundene Sender- oder Empfaenger-Transform konnte nicht installiert werden. |
| `media_e2ee_authentication_failed` | Ein empfangener Frame war fuer die aktive Session bzw. Epoche nicht authentisch; der Pfad schliesst fehlersicher. |
| `microphone_permission_denied` | Die Mikrofonfreigabe wurde im Browser abgelehnt. |
| `publication_permission_denied` | Kamera- oder Bildschirmfreigabe wurde abgelehnt. |
| `webrtc_session_not_open` | Die Medienfreigabe wurde vor dem Aufbau der Peer-Verbindung gestartet. |

Die Hub-Feature-Flags in den Compose-Dateien bleiben absichtlich `false`.
Public Pair schaltet diese Hub-Flags nicht um, sondern verwendet ausschliesslich
den separaten signierten Medienvertrag und die bestaetigte
Browser-zu-Browser-Bindung.
Worker erhalten weiterhin weder die Medienfreigabe noch Audio-/Videodaten;
dadurch bleibt die Hub-Worker-Grenze erhalten.
