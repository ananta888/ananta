# Artifact P2P Docker Profiles

## Ziel

Optionale Docker-Profile fuer P2P-Hilfsdienste bereitstellen, ohne den Hub als Policy-Instanz zu ersetzen.

## Profil: `artifact-p2p-signaling`

- Stellt Signaling fuer Session-Aufbau bereit.
- Haelt keine Artefaktinhalte als Autoritaetsdaten.
- Kann durch alternativen Signaling-Dienst ersetzt werden.

## Profil: `artifact-p2p-turn`

- Stellt TURN-Relay fuer schwierige NAT-Szenarien bereit.
- Transporthilfe, keine Berechtigungsinstanz.
- Nur aktiv, wenn P2P-Topologie TURN wirklich benoetigt.

## Bezug zum Game-P2P-Modell

- Nutzt dieselben Konzeptbausteine (Signaling/STUN/TURN, DataChannel-Session).
- Keine Kopplung an Spiel-spezifischen Code oder Spielzustand.
- Artefakt-Policy bleibt in derselben Hub-Governance wie bei Hub/Object-Storage.

## Secret-Handling

- TURN-Credentials und API-Secrets nur per Environment oder Secret-Store.
- Keine hardcodierten Zugangsdaten in Compose/Code.
- Rotation von Credentials muss ohne Image-Neubau moeglich sein.
