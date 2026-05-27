# KRITIS Sandboxed Test Environment Rollout (K3-SBX-T08)

## Ziel

Tests laufen schrittweise in einer sandboxed Ausführungsumgebung, bis der Standardbetrieb vollständig abgesichert ist.

## Rollout-Phasen

1. **dry_run**
   - Policy wird ausgewertet und protokolliert.
   - Keine harte Blockade, nur Messung von Treffern/Abweichungen.
2. **canary**
   - Selektive Durchsetzung auf Teilmengen (kritische Flows zuerst).
   - Incident/False-Positive Monitoring mit schneller Rückrolloption.
3. **full**
   - Policy-Throughput vollständig erzwungen.
   - Produktionsstandard für Terminal-/Command-Ausführung.

## Steuerung über Policy

`sandbox_policy.test_rollout`:
- `enabled` (bool)
- `default_environment` (`sandboxed`)
- `phases` (`dry_run`, `canary`, `full`)

## Erfolgskriterien

- Keine unkontrollierten Durchläufe von High-Risk-Kommandos ohne passende Isolationsklasse.
- Keine Workspace-Escapes in Terminalpfaden.
- Reproduzierbare, auditable Policy-Entscheidungen pro Ausführung.
