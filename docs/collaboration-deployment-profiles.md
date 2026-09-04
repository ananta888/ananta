# Collaboration deployment profiles

The collaboration native core is composed by the Hub. Durable, live and
optional bridge adapters remain separate and workers never compose or route
other workers.

| Profile | Durable state | Live path | Bridge | Gate state |
| --- | --- | --- | --- | --- |
| `local` | SQLite | Hub relay | disabled | ready for local technical use |
| `single_hub` | SQLite | Hub relay | disabled | ready; no HA claim |
| `multi_hub` | PostgreSQL shared event CAS | shared relay required | disabled | unverified |
| `sfu_enabled` | shared CAS required | SFU/TURN | disabled | unverified |
| `buzz_enabled` | shared CAS required | shared relay required | Buzz | unverified |

The local default needs no external secret, Buzz relay or SFU. Optional
profiles name secret references only; secret values never enter profile
objects, events, logs or metrics.

## HA and split brain

`multi_hub`, `sfu_enabled` and `buzz_enabled` must remain unavailable until a
shared CAS store, outbox, presence registry and cache are configured and their
failure behavior has a Hub-reserved runtime gate. A Hub that cannot reach the
shared authority fails closed for writes. It must not fall back to a local
writer, merge divergent workspace sequences, or promote local observations to
release evidence. Read-only degraded behavior may expose the last verified
checkpoint with an explicit stale marker.

The PostgreSQL event/outbox/checkpoint migration and repository are available
as the durable boundary and have a real concurrent database gate. Multi-Hub
still remains `unverified` because presence, policy/cache composition and
split-brain runtime behavior must be shared and tested as one deployment.

## Capacity boundaries

`configured_safety_caps` are defensive limits, not throughput claims. The
local profile records only a local technical observation. Participant, room,
event, queue, search and bridge capacity claims require reproducible runs on
the named target profile and hardware, with pre-reserved Hub `RUN_*` identity.
Until then their capacity evidence stays `unverified`.
