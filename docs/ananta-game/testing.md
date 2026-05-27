# Strategy Game Tests

## Golden Map

- Golden-JSON: `tests/golden/ananta-game/demo-gamemap.json`
- Snapshot-Test stellt stabilen JSON-Export sicher.

## Priorisierte Testbereiche

1. **ContextAegis**: local-only/secret/hidden/default-deny
2. **ArtifactGuard**: verified vs missing-evidence vs failed vs stale
3. **AegisFlow/AegisHub**: success/retry/rollback, Hub-Ownership, no worker-to-worker orchestration
4. **TrustWeave**: positive/negative/neutral events und deterministischer Graph-Export

## Negativfaelle

- Cloud-Agent darf keine local-only/secret Territorien voll sichtbar erhalten.
- Unverified Tasks duerfen keine Sieg-/Completion-Bedingung erfuellen.
- Trust darf nicht durch undefinierte Events willkuerlich steigen.
