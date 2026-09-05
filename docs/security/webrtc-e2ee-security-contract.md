# WebRTC-/Pair-View-E2EE-Sicherheitsvertrag (v1)

Der Hub bleibt die Autorität für Mitgliedschaft, Berechtigungen, Epoch und
Negotiation. Er signiert Peer- und Gruppen-Key-Pakete, erhält aber weder den
ECDH-Private-Key noch abgeleitete Content-Keys. Relay und ein späterer SFU
routen deshalb ausschließlich opake `secure_envelope.v1`-Nachrichten.

## Verbindliche Grenzen

- Der kanonische Rechtevertrag enthält nur `chat`, `view_tui`,
  `remote_cursor`, `artifact_share` und `remote_control`. Die v0-Aliase
  `cursor`, `control`, `artifact_view` und `annotation` laufen am
  1. Januar 2027 UTC aus. Unbekannte, nicht-boolesche und widersprüchliche
  Angaben werden vollständig abgelehnt.
- Browser-Private-Keys sind non-extractable und werden als `CryptoKey` in
  IndexedDB gespeichert. Der alte PKCS8-Wert aus Local Storage wird gelöscht,
  niemals importiert, und löst eine Peer-Neubindung aus.
- AES-256-GCM authentifiziert Scope, Sender, Empfänger, Epoch, Sequenz, Key-ID,
  Payloadtyp, Ablaufzeit, Traffic-Klasse und Contract-Digest. Nonces dürfen je
  Key/Epoch nur einmal vorkommen.
- Sensible Pair-View-Daten werden erst nach Hub-signierter Peer-Bindung und
  beidseitiger Key-Confirmation gesendet. Ohne aktuelle Epoch oder bestätigten
  Encrypter bleibt der Pfad geschlossen. Es gibt keinen produktiven Stub-
  Provider.
- Join, Leave, Revoke, Permission-Revoke und Hub-Ownership-Wechsel rotieren die
  autoritative Epoch. Persistente Replay-Fenster verhindern, dass Reconnects
  alte Control-, Semantic- oder Bulk-Nachrichten erneut gültig machen.
- Ein Gruppen-Key wird ausschließlich clientseitig installiert. Der Hub
  signiert Member-Set-Digest, Publication, Epoch, Gültigkeit und opake
  Key-Paket-Referenzen; Join gewährt keinen Vergangenheits-Key und Revoke
  erzwingt Purge bis zur Rekey-Deadline.

## Downgrade

Die Standardpolicy erlaubt keinen Downgrade von `strict_e2ee`. Falls ein
Deployment ihn explizit aktiviert, muss die Hub-Policy eine sichtbare,
tenant-/user-/scope-gebundene, ablaufende und widerrufbare Einwilligung prüfen.
Offer, Answer, Algorithmen, Payloadklassen und Epoch fließen in den finalen
Contract-Digest ein; Normalisierungs- oder Flag-Manipulation ändert den Digest
und verhindert die Aktivierung.

## Medienformat, Ableitung und Rotation

Das bestehende `ANME`-Frameformat ist ausschließlich der explizite
`legacy_anme_v1`-Compatibility-Adapter. Es ist keine RFC-9605-SFrame-
Implementierung. `sframe_rfc9605` darf nur gewählt werden, wenn ein eigener
nativer Adapter die Capability meldet; unbekannte Formate und ein Wechsel vom
ausgehandelten SFrame-Pfad zu ANME werden fail-closed abgelehnt. Für
Cross-PeerConnection-Medienrelay bleibt der portable Browserpfad weiterhin
`no_go`.

Gruppenmedien-Schlüssel werden mit HKDF-SHA-256 nach Tenant, Room, Publication,
Sender, Audio/Kamera/Screenshare/Daten und Key-Epoch getrennt. Die resultierenden
AES-GCM-Schlüssel sind non-extractable. Eine Rotation aktiviert zuerst die neue
Epoch, sperrt die alte Sender-Epoch sofort und fordert für Video einen Keyframe
an. Eine alte Receiver-Epoch darf nur in einem begrenzten Receive-only-Fenster
weiterleben; bei `revoke` ist dieses Fenster immer null.

## Gate

`python scripts/run_webrtc_crypto_gate.py` führt dieselbe versionierte
Negativmatrix aus, die Python- und TypeScript-Contracttests lesen. Der
maschinenlesbare Report enthält nur Vektorname, öffentlichen Reason-Code,
Fixture-Hash und Pass/Fail, niemals Payload, Schlüssel oder Nonce.
