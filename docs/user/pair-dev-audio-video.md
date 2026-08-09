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

1. Im AI-Snake-Fenster `Pair Dev` oeffnen und mit Keycloak anmelden.
2. Eine Share-Session erstellen oder ihr beitreten und warten, bis die
   WebRTC-Verbindung steht.
3. Im Medienbereich **auf beiden Rechnern bzw. Browsern** `Ordinary
   Audio/Video` aktivieren. Bei Public Pair bestaetigen beide Seiten damit den
   schluesselgebundenen Medien-E2EE-Pfad; bei einer privaten Session wird die
   Hub-Freigabe geprueft. Solange nur eine Seite aktiviert hat, bleibt der
   Status erwartungsgemaess bei `awaiting-peer`.
4. `Mikrofon freigeben`, `Kamera freigeben` oder `Bildschirm freigeben`
   bewusst anklicken und die Browserabfrage bestaetigen.
5. Empfangene Audiospuren erscheinen im Remote-Audio-Player. Empfangene
   Kameras und Bildschirmfreigaben werden als eigene Remote-Videos angezeigt.

`Bildschirm freigeben` uebertraegt aktuell nur das Bild. Fuer Ton parallel
`Mikrofon freigeben` verwenden; Browser- oder Systemton der Bildschirmquelle
wird noch nicht mitgesendet.

Das Oeffnen oder Aktivieren des Pair-Dev-Medienbereichs fordert noch keine
Browserrechte an.
Stummschalten, Stoppen und Wechseln einer lokalen Quelle bleiben explizite
Benutzeraktionen. Das Verlassen bzw. Widerrufen der Session beendet die
sessiongebundenen lokalen Publikationen.

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
| `public_ordinary_media_e2ee_awaiting_peer` | Das Gegenueber muss `Ordinary Audio/Video` ebenfalls aktivieren. |
| `public_ordinary_media_e2ee_negotiating` | Beide Browser haben zugestimmt und richten die Transforms gerade ein. |
| `public_ordinary_media_e2ee_unavailable` | Der sichere Public-Medienpfad ist in diesem Browser nicht verfuegbar. |
| `media_e2ee_transform_unsupported` | Der Browser stellt keinen unterstuetzten WebRTC-Encoded-Transform bereit. |
| `public_media_runtime_unsupported` | Der Browser unterstuetzt den standardisierten `RTCRtpScriptTransform`-Pfad nicht vollstaendig. |
| `public_media_contract_not_ready` | Der separate signierte Medienvertrag fehlt noch; Sicherheitsbestaetigung abwarten oder mit zwei aktualisierten Browsern eine neue Session erstellen. |
| `public_media_contract_missing` | Fuer diese Session wurde kein separater signierter Medienvertrag ausgehandelt; mit zwei aktualisierten Browsern eine neue Session erstellen. |
| `public_media_contract_expired` | Der Public-Medienvertrag ist abgelaufen; eine neue Pair-Session erstellen. |
| `public_media_transform_not_prepared` | Der WebRTC-Pfad bzw. seine festen E2EE-Medienslots sind noch nicht vorbereitet. |
| `public_media_peer_consent_pending` | Das Gegenueber muss `Ordinary Audio/Video` ebenfalls aktivieren. |
| `public_media_peer_activation_pending` | Das Gegenueber muss `Ordinary Audio/Video` ebenfalls aktivieren. |
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
