# Worker Command Execution

Dieses Dokument beschreibt den Shell-Execution-Pfad im Hub-Worker-Modell.

## Verantwortungen

- Hub: orchestriert Task, Policy und Guardrail-Entscheidungen.
- Worker: fuehrt delegierte Segmente/Tool-Calls aus, aber orchestriert keine weiteren Worker.

## Guardrail-Pipeline

1. Command wird normalisiert (transcription repair).
2. `ShellCommandAnalyzer` bewertet Operatoren quote-aware.
3. Bei Chains validiert `SegmentPreflightValidator` alle Segmente vor Ausfuehrung.
4. Approval-/Risk-/Scope-Regeln werden segmentweise angewendet.
5. Erst danach erfolgt Segmentausfuehrung mit `;`, `&&`, `||`-Semantik.

## Complex Shell Mode

- `shell_command_mode: "pipeline"` signalisiert Ausfuehrungsintention im Task-Kontext.
- Tatsaechlich aktiviert wird der Modus nur, wenn `shell_command_policy.allow_complex_shell_mode == true`.
- Damit bleibt Least-Privilege erhalten: ein Task kann die globale Policy nicht selbst aufweichen.

## Audit und Nachvollziehbarkeit

- Block-Events werden in Task-History und Execution-Audit festgehalten.
- Bei Chain-Fehlern enthalten Diagnosen Segment-Metadaten (Preview, Index, Reason Codes).
