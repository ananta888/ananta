# ContextAegis

ContextAegis modelliert im Strategy Game die Sichtbarkeit von Code-Territorien als Fog-of-War mit Default-Deny.

## Kernregeln

1. Unbekannte Territorien sind `hidden` und `deny`.
2. `secret`-Territorien sind `redacted` statt voll sichtbar.
3. `local_only`-Territorien sind fuer Cloud-Rollen gesperrt.
4. Freigabe erfolgt nur ueber explizite Rollen-/Policy-Regeln.

## Entscheidungszustaende

- `allow` + `visible`
- `deny` + `hidden`
- `redacted` + `redacted`

Keine heuristische Freigabe ohne Policy.
