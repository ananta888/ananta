# Shell Command Policy Migration Note

## Scope

Diese Aenderung betrifft den Shell-Guardrail-Pfad fuer Command-Chains und Shell-Tool-Calls.

## Breaking/Non-Breaking

- API- und Service-Signaturen bleiben rueckwaertskompatibel (`command_analysis` nur optional erweitert).
- Default-Verhalten ist non-breaking fuer erlaubte Chains (`;`, `&&`, `||`).
- Security-Verhalten bleibt streng fuer `|`, `` ` ``, `$(`, `${`, `<<`.

## Was sich geaendert hat

- Chain-Kommandos werden parser-/policy-basiert und segmentweise vorab validiert.
- Approval- und Risk-Bewertung koennen segment-aware aggregieren.
- Shell-Tool-Calls (`shell_execute`, `run_command`, `execute_command`, `bash`) nutzen dieselbe Analyse.
- Complex Shell Mode (`pipeline`) wird nur aktiviert, wenn:
  1. Task-Kontext `shell_command_mode="pipeline"` setzt und
  2. globale Policy `shell_command_policy.allow_complex_shell_mode=true` erlaubt.

## Operator-Overrides

Strengere Profile koennen erlaubte Chain-Operatoren reduzieren:

```json
{
  "shell_command_policy": {
    "allow_chain_operators": ["&&"]
  }
}
```

Details: `docs/security/shell-command-policy.md`.
