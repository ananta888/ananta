# State Ownership Matrix (OSS Core)

## Purpose

This document defines ownership and writer/read rules for core runtime states. It protects the hub-worker boundary:

- Hub stays the control-plane owner for orchestration-critical server states.
- Workers can write only through hub-governed execution contracts.
- Clients can request through public APIs and render state, but cannot directly write server-owned control states.

## Core ownership model

The machine-readable source is `data/state_ownership_matrix.json`.

| State type | Owner | Server-owned | Allowed writers | Notes |
| --- | --- | --- | --- | --- |
| goal | hub | yes | hub | Client input enters via API request, not direct state writes. |
| plan | hub | yes | hub | Planning remains control-plane logic. |
| task | hub | yes | hub | Task lifecycle is hub-governed. |
| execution | hub | yes | hub, worker | Worker execution updates are mediated by hub contracts. |
| approval | hub | yes | hub | Policy/approval decisions remain centralized. |
| artifact | hub | yes | hub, worker | Workers produce results; hub persists governed artifact state. |
| audit | hub | yes | hub | Append-oriented event stream, not ordinary mutable state. |
| verification | hub | yes | hub, worker | Verification evidence is linked through hub-governed flow. |
| repair | hub | yes | hub | Repair/retry remains an execution-like controlled path. |
| client_ui_state | client | no | client | Local presentation state only. |

## Invariants

1. Every core state type has exactly one owner.
2. Server-owned states are never client-owned.
3. Writers are explicit and non-empty when a state is mutable.
4. Audit is append-only and not treated as mutable business state.

## SOLID alignment

- **SRP:** state ownership concerns are separated from execution implementation details.
- **DIP:** workers depend on hub contracts to mutate execution-related state instead of direct persistence coupling.
