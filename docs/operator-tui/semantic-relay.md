# Semantic relay operations

The semantic relay is an optional Hub-owned fallback for encrypted WebRTC
DataChannel messages. It is not a task queue and never decrypts application
content. Direct peer-to-peer delivery remains preferred; a relay entry is only
accepted for an active strict-E2EE share session, a current epoch, two active
members, an allowed direction, a current permission and a confirmed peer key.

## Consistency and deployment

Production multi-Hub deployments use the existing shared PostgreSQL database.
`semantic_relay_envelopes` stores bounded opaque deliveries and
`semantic_relay_cursors` stores one monotone cursor per tenant, session,
audience and traffic class. A PostgreSQL advisory transaction lock serializes
the quota check, cursor allocation and append across Hub replicas. The
in-memory repository implements the same port and limits for unit tests only;
it is not a production multi-Hub backend.

Run migrations before enabling any semantic feature:

```bash
alembic upgrade head
alembic heads
```

There must be exactly one head. A rollback of the wire metadata is supported by
`alembic downgrade d8e9f0a1b2c3`; a full relay rollback is supported by the
preceding migration. Existing Pair chat/view endpoints remain additive
compatibility adapters over the same repository.

## Feature gates and failure behavior

All semantic media flags default to false. `control` and content-free
`diagnostic` relay traffic are available to an authorized strict-E2EE session;
the remaining classes require their corresponding Hub flag:

| Traffic class | Required flag |
|---|---|
| `transcript`, `audio_recovery` | `SEMANTIC_SPEECH_RUNTIME_ENABLED` |
| `visual_semantic` | `SEMANTIC_VISUAL_CAPTURE_ENABLED` |
| `evidence_bulk` | `PEER_EVIDENCE_SYNC_ENABLED` |

Unknown classes, stale epochs, missing guards, missing confirmation, expired
messages and disabled features fail closed. A browser polls each enabled class
with an isolated cursor, dispatches a message ID once and acknowledges only
after schema and context validation. Disconnect, session close and epoch change
clear polling, cursors and bounded duplicate state.

## Incident checks

1. Confirm that the share session is active and strict E2EE is negotiated.
2. Confirm current membership epoch and bilateral permissions; never bypass a
   stale-epoch or key-confirmation response.
3. Check reason-code counters by traffic class and state. Telemetry must contain
   no ciphertext, plaintext, transcript, frame, peer ID or session ID.
4. Check PostgreSQL availability and that all Hub replicas point to the same
   database. SQLite is only a local/single-Hub option.
5. Run the deterministic resource gate:

   ```bash
   python scripts/run_semantic_transport_gate.py --verify
   python scripts/e2e/semantic_relay_multi_hub_e2e.py --execute-live
   ```

   The second command starts the digest-pinned PostgreSQL image, migrates it to
   the single current Alembic head, races two independent Hub processes and
   kills one with `SIGKILL` after committed writes. It then proves restart
   recovery plus repeatable acknowledge, revoke, expiry and session cleanup.
   Its tracked report contains only fixed topology/count metadata and checks;
   the ephemeral database, credentials and opaque envelopes are destroyed.

6. To contain an incident, disable the affected semantic flag. Ordinary WebRTC
   and the existing bounded Pair compatibility path remain available. Ending a
   session idempotently revokes all queued relay entries.

Do not inspect or log stored ciphertext during diagnosis. Use only bounded
counts, byte totals, stable reason codes and latency buckets.
