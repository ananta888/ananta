# Provider-neutral JMAP integration-test stack

The local JMAP acceptance boundary is entirely repository-owned. It does not
download or start a third-party mail server, container image, management API,
or vendor-specific extension.

## Local contract

`ContractJmapAdapter` implements the standard JMAP Core and JMAP Mail responses
needed by this track. Each instance owns isolated deterministic state with:

- one personal mail account,
- Inbox and Projects mailboxes,
- a fixed two-message thread,
- stable message, thread, blob, mailbox, query, and state identifiers,
- explicit body values and keyword mutation.

The focused fixture contract is:

```bash
pytest -q -W error tests/e2e/test_jmap_fixture_contract.py
```

The production-composition contract is:

```bash
pytest -q -W error tests/e2e/test_jmap_ananta_composition_contract.py
```

It exercises the real Hub task submission, lease/fencing, production worker
composition, provider routing, JMAP discovery/sync, body-grant enforcement,
mutation, explicit IMAP fallback selection, migration, and restore. The
injected adapter is the narrow transport seam; production policy and business
logic are not replaced.

## Security and cleanup

The only retained credential fixture is a fixed non-production password under
`tests/fixtures/jmap/secrets/`. Results are checked for credential and body
leaks. Adapter state, temporary stores, the intent endpoint, and task state are
owned by the test process and discarded during fixture teardown. There are no
containers, volumes, networks, downloaded artifacts, or external connections
to clean up.

## External provider evidence

The repository-owned contract proves Ananta behavior but does not claim
interoperability with any specific server product. A real provider smoke is a
separate deployment gate supplied by an operator through a compatible JMAP
session and short-lived credentials. It must emit only redacted evidence with
actually supplied `RUN_*` identifiers and must never auto-download or start a
server.

The smoke requires a dedicated disposable test account because it performs and
then restores one keyword mutation:

```bash
export RUN_JMAP_LIVE_PROVIDER_E2E=1
export ANANTA_JMAP_LIVE_DEDICATED_ACCOUNT_ACK=1
export ANANTA_JMAP_LIVE_SESSION_URL=https://mail.example.test/.well-known/jmap
export ANANTA_JMAP_LIVE_USERNAME=alice@example.test
export ANANTA_JMAP_LIVE_CREDENTIAL='<short-lived credential>'
export ANANTA_JMAP_LIVE_RUN_ID=RUN_<provided-id>
pytest -q -W error tests/e2e/test_jmap_live_provider.py
```

Optional settings are `ANANTA_JMAP_LIVE_AUTH_MODE` (`basic` or `bearer`),
`ANANTA_JMAP_LIVE_PROVIDER_ACCOUNT_ID`, and
`ANANTA_JMAP_LIVE_EVIDENCE_PATH`. The default evidence path is
`artifacts/e2e/jmap-mail-gate.json`. Missing opt-in skips the test; an incomplete
opted-in configuration fails with a stable reason code.
