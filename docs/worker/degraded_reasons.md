# Worker Degraded Reasons

Stabile, maschinenlesbare Degraded-Reasons:

- `policy_denied`
- `hub_approval_token_missing`
- `schema_invalid`
- `budget_exhausted`
- `unsafe_command`
- `prompt_injection_blocked`

## Operator Mapping

- `policy_denied`: Policy/Scope pruefen, keinen Retry ohne Policy-Aenderung.
- `hub_approval_token_missing`: passendes Approval-Token fuer Task/Capability/Context ausstellen.
- `schema_invalid`: Artefakt-Vertrag korrigieren, Producer/Consumer-Versionen pruefen.
- `budget_exhausted`: Profil/Budgets anpassen oder Task in kleinere Schritte zerlegen.
- `unsafe_command`: erlaubte Alternativstrategie verwenden (read-only Diagnose oder Tool-Call).
- `prompt_injection_blocked`: Artefaktquelle bereinigen, hostile Context ausfiltern.
