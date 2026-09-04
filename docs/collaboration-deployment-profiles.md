# Collaboration deployment profiles

The collaboration native core is composed by the Hub. Durable, live and
optional bridge adapters remain separate and workers never compose or route
other workers.

| Profile | Durable state | Coordination | Live path | Bridge | Gate state |
| --- | --- | --- | --- | --- | --- |
| `local` | SQLite | process local | Hub relay | disabled | ready for local technical use |
| `single_hub` | SQLite | SQLite/single Hub | Hub relay | disabled | ready; no HA claim |
| `multi_hub` | PostgreSQL shared event CAS | PostgreSQL shared live/presence/cache CAS | shared relay required | disabled | unverified |
| `sfu_enabled` | shared CAS required | shared coordination required | SFU/TURN | disabled | unverified |
| `buzz_enabled` | shared CAS required | shared coordination required | shared relay required | Buzz | unverified |

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

The PostgreSQL event/outbox/checkpoint and live coordination repositories have
a real concurrent database gate. Cursor, remote-control grant, presence and
generic cache keys include tenant and workspace in their primary keys. Grant
and cache writes use database advisory fencing plus revision CAS. Migration
execution itself is serialized by a PostgreSQL advisory lock. Multi-Hub still
remains `unverified` because the selected deployment and its split-brain
behavior must be tested as one Hub-reserved runtime run.

## Capacity boundaries

`configured_safety_caps` are defensive contract limits, not throughput claims.
No profile publishes participant, room, event, queue, search or bridge capacity
claims from those values. The local profile records only a local technical
observation. Production capacity claims require reproducible runs on the named
target profile and hardware, with a pre-reserved Hub `RUN_*` identity. Until
then their capacity evidence stays `unverified`.
