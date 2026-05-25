# Artifact Transfer Configuration

## Modusmatrix

Unterstuetzte Modi:

- `hub_storage`
- `webrtc_p2p`
- `manual_export`
- `disabled`

## Policy-Kopplung

- Policy kann Modus pro Datenklassifikation begrenzen.
- Beispiel: `local_only` -> nur `hub_storage` lokal oder `disabled`.
- Unsichere oder experimentelle Modi sind nicht Default.

## Infrastrukturparameter

- Externe Signaling/STUN/TURN-Endpunkte sind konfigurierbar.
- Keine hardcodierten Service-URLs.
- Secrets (TURN Credentials, API Tokens) nur via Environment/Secret-Store.

## Sicherheitsdefaults

- Default bleibt konservativ (`hub_storage` oder `disabled` je Deployment).
- `webrtc_p2p` muss explizit aktiviert werden.
- `manual_export` ist ueber UI/CLI klar als Risikooption zu kennzeichnen.
