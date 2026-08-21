# Generic knowledge claim precedence policy

Version: knowledge_claim_precedence.v1

## Purpose

Precedence orders human review. It does not decide which claim is true, remove evidence or hide a candidate.

## Scope gates

Claims are comparable only when project, scope, normalized subject and normalized predicate agree. A mismatch produces out_of_scope or unrelated, not a contradiction.

## Non-conflicts

- Equal normalized payloads are confirmations or exact duplicates.
- A target and an actual value are compatible parallel assertions.
- Non-overlapping effective periods are temporally distinct.
- Incompatible units are insufficient evidence for a numeric contradiction until a conversion policy is explicitly supplied.

## Candidate conflicts

For overlapping comparable claims, deterministic validators identify:

- different numeric values with the same normalized unit;
- affirmation versus negation;
- known incompatible status pairs;
- otherwise different normalized values.

Semantic similarity is never a truth validator. It may produce a duplicate or conflict candidate only when an injected, versioned profile meets its configured threshold. The candidate records profile, threshold, score and evidence.

## Trust, freshness and coverage

Source trust and freshness affect review order and severity only. They never suppress either side or close a conflict.

If either side has partial or unknown coverage, severity is unknown and the UI must expose the coverage limitation. Missing evidence under incomplete coverage is never converted into a false negative or zero.

## Human authority

Only an authenticated human actor can select keep_left, keep_right, keep_both, request_correction or dismiss_not_conflict. The command is bound to the exact conflict version, both claim digests and the computed basis digest. Optional dual approval requires a distinct second actor.

Corrections remain pending_reingest after a preference decision. Resolution requires a new complete run with exact new claims, except keep_both and dismiss_not_conflict, which resolve the classification rather than changing source truth.
