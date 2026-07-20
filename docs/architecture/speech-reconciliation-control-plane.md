# Speech reconciliation control plane

Speech reconciliation is a Hub-owned offline workflow. The Hub admits inputs,
allocates budgets, creates parent/attempt tasks, issues leases, accepts fenced
checkpoints/results and decides whether dataset materialization or a separate
training delegation may follow. A worker executes one admitted attempt; it
cannot mutate job state, create another task, contact a peer, or authorize
training.

| Plane | Queue / priority | Container | data access | SLO boundary |
| --- | --- | --- | --- | --- |
| Live audio/transcript | realtime / highest | voice runtime | bounded live buffers | never waits for offline work |
| Peer sync | peer-sync / high | Hub + authenticated peer transport | consent-scoped evidence | bounded independently |
| Reconciliation | offline / low | dedicated non-root worker | encrypted admitted artifacts | paused under live pressure |
| Dataset | Hub persistence | Hub | immutable admitted evidence | transactional publication |
| Training | separate training queue | dedicated training worker | immutable dataset only | separately admitted/budgeted |

Lifecycle state and processing stage are separate. States are `queued`,
`running`, `paused`, `cancel_requested`, `completed`,
`dataset_only_completed`, `failed`, `cancelled`, and `expired`. Checkpoints do
not grant authority: every publication must match the current attempt fence,
consent/revocation epoch, input manifest, policy and monotone budget ledger.
Realtime and offline queues use independent capacity pools; live pressure can
pause offline scheduling without acquiring a realtime slot.
