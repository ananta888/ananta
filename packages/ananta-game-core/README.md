# Ananta Game Core

Framework-independent TypeScript implementation of the local 19-hex prototype.
No runtime dependencies, network, randomness, persistence or worker orchestration.

```bash
npm ci
npm test
```

Use Node 24 (the repository frontend runtime). `npm test` compiles in strict mode
and runs self-contained Node tests, including full matches and replay.

```ts
import { createSession, recordAction, exportReplay, importReplay } from './src';

const first = createSession();
const { session, result } = recordAction(first, {
  type: 'MOVE', player: 'sun', turn: 1, unitId: 'sun-naga', to: 'H09',
});
const restored = importReplay(exportReplay(session));
```

The Angular frontend uses the same source under `/strategy-game` after normal
Ananta login. Two people play on the same device. Export the move log before
reloading to preserve a match. JSON import rebuilds from the fixed initial
position; it never accepts an arbitrary state or grants Hub authority.

Rules and balance are provisional. See the [short rules](../../docs/examples/ananta-game/brutal_mvp_rules.md),
[model](../../docs/examples/ananta-game/game_core_model.md) and
[test plan](../../docs/examples/ananta-game/test_plan.md).
