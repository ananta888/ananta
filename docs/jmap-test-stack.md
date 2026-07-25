# Stalwart JMAP integration-test stack

The JMAP test stack provides a deterministic local boundary for provider
integration tests. It is separate from the production compose profile and owns
its containers, volumes, network, accounts, mailboxes, and messages through the
Compose project `ananta-jmap-test`.

## What is automated

- Bootstrap through `x:Bootstrap/set`, with certificate and DKIM generation off.
- Internal test domain `example.test`.
- Test accounts `alice@example.test` and `bob@example.test`.
- Deterministic mailboxes, a two-message thread, keywords, and one attachment.
- JMAP verification after seeding.
- Real Hub task submission, account lease/fencing, production worker composition,
  JMAP discovery/sync, body-grant enforcement, and mutation.
- Auto-protocol fail-closed behavior and the explicit IMAP fallback selection.
- Fixture-backed migration and approval-bound restore.
- Teardown of containers, volumes, and networks.
- A zero-resource assertion based on the Compose project label.
- An in-process contract E2E that exercises the same bootstrap and seed flow
  without Docker, an image pull, or network access.

The credentials in `tests/fixtures/jmap/secrets/` are deliberately fixed,
non-production fixture values. The runner reads them from files, passes the
bootstrap credential through the child process environment, never places a
secret in a command argument, and emits only bounded status summaries.

## License gate

The pinned upstream Stalwart image currently carries upstream AGPL-3.0 and
Stalwart Enterprise License v2 terms. Whether the project may download and run
that image is an organizational decision, not a technical acceptance decision.
Consequently, every command that touches a live server requires:

```bash
export ANANTA_STALWART_TEST_LICENSE_ACK=1
```

This variable records only that the required organizational decision happened.
The repository does not make or imply that decision.

The deterministic contract remains available without the acknowledgement:

```bash
python scripts/stalwart_jmap_test_stack.py contract
pytest -q -W error tests/e2e/test_stalwart_jmap_fixture_contract.py
```

## Live lifecycle

Start, bootstrap, seed, and verify:

```bash
ANANTA_STALWART_TEST_LICENSE_ACK=1 \
  python scripts/stalwart_jmap_test_stack.py up
```

Reset all state, start again, seed, and verify:

```bash
ANANTA_STALWART_TEST_LICENSE_ACK=1 \
  python scripts/stalwart_jmap_test_stack.py reset
```

Verify an already seeded server:

```bash
ANANTA_STALWART_TEST_LICENSE_ACK=1 \
  python scripts/stalwart_jmap_test_stack.py verify
```

Teardown is always allowed because cleanup must not be blocked by governance:

```bash
python scripts/stalwart_jmap_test_stack.py down
```

The command fails if any container, volume, or network with the project label
remains.

## Live pytest gate

The real Docker E2E is skipped by default. Both variables are required:

```bash
ANANTA_STALWART_TEST_LICENSE_ACK=1 \
RUN_STALWART_LIVE_E2E=1 \
  pytest -q -W error tests/e2e/test_stalwart_live_jmap.py
```

The test always invokes teardown in `finally`. A failed teardown remains a test
failure rather than silently leaving resources behind.

Without the live opt-in,
`tests/e2e/test_stalwart_ananta_composition_contract.py` runs the same Hub and
production worker/provider composition against an in-process JMAP transport and
an in-process Hub intent endpoint. It asserts that task results contain only
reference IDs, reason codes, and bounded counters, never credentials or message
content.

## Fixture maintenance

`tests/fixtures/jmap/stalwart-seed.json` is the source of truth. Fixture files
must remain deterministic, environment-independent, minimal, and free of
production credentials. Message dates, IDs, MIME boundaries, keywords, and
attachment bytes are intentionally fixed so contract results are reproducible.
