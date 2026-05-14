# Improvement Loop State Machine (GEC-T017)

Hub-controlled loop states:

- `propose`
- `execute`
- `verify`
- `critique`
- `repair`
- terminal: `complete`, `needs_review`, `blocked`

## Allowed transitions

- `propose -> execute|needs_review|blocked`
- `execute -> verify|repair|needs_review|blocked`
- `verify -> complete|critique|needs_review|blocked`
- `critique -> repair|needs_review|blocked`
- `repair -> execute|needs_review|blocked`

## Guardrails

- Max loop attempts (`max_improvement_loops`) are enforced by hub state transition checks.
- Terminal states are immutable.
- Security denied paths are expected to end in `blocked` unless explicit safe re-plan policy exists.

