# Pair Dev: Audio und Video

Pair Dev verwendet fuer normale Zwei-Personen-Gespraeche den bestehenden
WebRTC-Pfad. Der Hub bleibt dabei die zentrale Autoritaet fuer Netzwerkprofil,
Session, Mitgliedschaft und Feature-Freigabe. Audio- und Videodaten werden
nicht als Worker-Tasks verarbeitet.

## Voraussetzungen

- Beide Personen sind im Pair-Dev-Bereich mit Keycloak angemeldet.
- Beide Browser sind derselben aktiven, nicht widerrufenen Share-Session
  beigetreten.
- Das aktive Netzwerkprofil verwendet den Transport `webrtc`; ein
  `hub_relay`-Fallback uebertraegt keine Audio- oder Videospuren.
- Der Hub wurde mit
  `ANANTA_ORDINARY_MEDIA_PUBLICATION_ENABLED=true` gestartet.
- Keycloak, Signaling sowie die konfigurierten STUN-/TURN-Dienste sind vom
  jeweiligen Browser erreichbar.
- Die Seite laeuft ueber HTTPS oder auf `localhost`, damit der Browser
  Mikrofon und Kamera freigeben darf.

Die weitergehenden Flags fuer semantische Bildaufnahme, Speech-Runtime und SFU
sind fuer einen direkten Zweier-Call nicht erforderlich und bleiben
standardmaessig deaktiviert.

## Ablauf in der Oberflaeche

1. Im AI-Snake-Fenster `Pair Dev` oeffnen und mit Keycloak anmelden.
2. Eine Share-Session erstellen oder ihr beitreten und warten, bis die
   WebRTC-Verbindung steht.
3. Im Medienbereich `Ordinary Audio/Video` aktivieren. Erst die bestaetigte
   Hub-/Pair-Projektion schaltet die Capture-Schaltflaechen frei.
4. `Mikrofon freigeben`, `Kamera freigeben` oder `Bildschirm freigeben`
   bewusst anklicken und die Browserabfrage bestaetigen.
5. Empfangene Audiospuren erscheinen im Remote-Audio-Player. Empfangene
   Kameras und Bildschirmfreigaben werden als eigene Remote-Videos angezeigt.

Das Oeffnen des Pair-Dev-Bereichs fordert nie selbststaendig Browserrechte an.
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
| `microphone_permission_denied` | Die Mikrofonfreigabe wurde im Browser abgelehnt. |
| `publication_permission_denied` | Kamera- oder Bildschirmfreigabe wurde abgelehnt. |
| `webrtc_session_not_open` | Die Medienfreigabe wurde vor dem Aufbau der Peer-Verbindung gestartet. |

Die Standardwerte in den Compose-Dateien bleiben absichtlich `false`. Eine
Aktivierung wird nur an den Hub durchgereicht, niemals an Worker; dadurch
bleibt die Hub-Worker-Grenze erhalten.
