# DSPy optimization operations

## Safe states

- disabled: reject admission; the rest of Ananta remains operational.
- unavailable: enabled Hub has no compatible worker; keep baseline active.
- degraded: worker version/capability mismatch; reject new runs.
- failed/cancelled: terminal run, never promote its candidate.
- blocked release: local checks may pass, but missing allowed evidence prevents rollout.

## Automatic recovery

| Detection | Automatic action |
| --- | --- |
| stale attempt or duplicate finalization | reject by attempt/revision fence |
| missing provider usage | fail the call; never treat cost as zero |
| call/token/cost/time limit | stop new calls and finalize failed/cancelled |
| worker loss | Hub retains admitted/running revision; retry requires a fresh attempt |
| corrupt/unsafe program state | reject before artifact write |
| evaluation regression | keep baseline active |
| canary stop criterion | atomically roll back the known previous digest |
| incompatible DSPy version | capability becomes degraded |

Rollback is an immutable registry revision, not an artifact rewrite. The
operator can disable admission immediately through configuration; policy may
also stop and roll back automatically. Neither tests nor production recovery
requires a person to unblock a waiting workflow.

Do not delete state databases or content-addressed artifacts during an
incident. Preserve them for revision, fencing and provenance analysis. Runtime
files under `data/` are not source and must not be committed.
