# TrustWeave

TrustWeave modelliert einen Trust-Graph zwischen Agenten, Territorien, Policies und Artefakten.

## Event-Regeln

- Positive Events (`verified_artifact`, `policy_compliant_success`) erhoehen Trust.
- Negative Events (`verification_failed`, `policy_violation`) senken Trust.
- Neutrale Events (`neutral_observation`) aendern Trust nicht.

Trust wird nur durch definierte Events angepasst.

## Export

Der Graph ist als JSON exportierbar:

- `nodes`: eindeutige Knoten-IDs
- `edges`: `source`, `target`, `trust_value`

Damit ist eine spaetere Darstellung in TUI/Web moeglich, ohne Entscheidungslogik im UI zu verankern.
