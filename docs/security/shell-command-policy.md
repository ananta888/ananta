# Shell Command Policy

Diese Policy beschreibt, wie Ananta Shell-Commands mit Chain-Operatoren bewertet, bevor etwas ausgefuehrt wird.

## Ziel

- legitime Testchains wie `pytest && git status` oder `python hello.py; python -c "..."` erlauben
- gefaehrliche oder schwer kontrollierbare Shell-Syntax standardmaessig blockieren
- alle Entscheidungen nachvollziehbar in Pipeline/History/Audit machen

## Default-Verhalten

Policy-Quelle: `agent_config.shell_command_policy`.

- erlaubte Chain-Operatoren: `;`, `&&`, `||`
- standardmaessig blockierte Operatoren: `|`, `` ` ``, `$(`, `${`, `<<`
- segmentweise Vorabpruefung: aktiviert
- quoted Operatoren (z. B. in `python -c "a; b"`): als Shell-Operatoren ignoriert
- complex shell mode (`pipeline`): nur bei explizit gesetztem `allow_complex_shell_mode: true`

## Ablauf

```mermaid
flowchart LR
    A[Raw Command] --> B[Transcription Repair]
    B --> C[ShellCommandAnalyzer]
    C -->|allowed| D[SegmentPreflightValidator]
    C -->|blocked| X[Guardrail Block]
    D -->|all segments allowed| E[Execution Semantics ; && ||]
    D -->|segment denied| X
```

## Sicherheitsinvariante

Wenn ein Segment denied ist, wird kein Segment ausgefuehrt.

Das gilt auch fuer Ketten wie:
- `python hello.py; rm -rf /tmp/x`
- `pytest && rm -rf /tmp/x`

## Beispiele

Erlaubt (Default):
- `pytest && git status`
- `pytest || echo failed`
- `python hello.py; python -c "from hello import greet; print(greet('World'))"`

Geblockt (Default):
- `cat file | grep secret`
- `echo $(cat .env)`
- `echo \`whoami\``
- `cat <<EOF`

## Konfiguration

Beispiel mit strengerer Chain-Policy:

```json
{
  "shell_command_policy": {
    "allow_chain_operators": ["&&"],
    "deny_operators": ["|", "`", "$(", "${", "<<"],
    "validate_segments_individually": true,
    "allow_quoted_operators": true,
    "allow_complex_shell_mode": false
  }
}
```

Beispiel fuer bewusst erlaubten Pipeline-Mode:

```json
{
  "shell_command_policy": {
    "allow_complex_shell_mode": true
  }
}
```

Hinweis: `shell_command_mode: "pipeline"` im Worker-Kontext allein reicht nicht. Die globale Policy muss es ebenfalls erlauben.
