# Scientific Skills Operations

This runbook covers the selective Scientific Skills adapter only. The Hub owns
catalog publication, runtime controls, selection, and audit. No operation in this
runbook installs an upstream package or executes an upstream file.

## Installation and security boundaries

- Keep `config/scientific-skills-catalog.json` immutable in the application
  image or a versioned configuration store.
- Keep mutable runtime control in the Hub container's durable `data/` volume;
  the default path is `data/scientific-skills-runtime-control.json` and is not a
  source artifact.
- Do not mount the runtime-control file into workers. Workers receive only tasks
  delegated by the Hub after policy admission.
- Do not clone K-Dense in a production container, run its scripts, install its
  dependencies, or provide scientific API credentials to catalog/update tests.
- The catalog revision and the runtime global switch must both be enabled. The
  runtime state defaults to disabled when missing and fails closed when malformed.

## Runtime control commands

Run these commands from the repository root. They are non-interactive and emit
one machine-readable JSON object. Always get the current revision first:

```bash
.venv/bin/python scripts/scientific_skill_runtime_control.py show
```

Enable or disable the complete pilot with compare-and-set:

```bash
.venv/bin/python scripts/scientific_skill_runtime_control.py global-enable \
  --expected-revision 0 --actor ops-automation --reason approved-pilot-window

.venv/bin/python scripts/scientific_skill_runtime_control.py global-disable \
  --expected-revision 1 --actor incident-automation --reason emergency-stop
```

Disable or re-enable one immutable catalog entry:

```bash
.venv/bin/python scripts/scientific_skill_runtime_control.py entry-disable \
  --entry-id skillentry_08923f34c517036a9ac1bfce65e1ba124034653d19d842edf2cecd5083b7c3ad \
  --expected-revision 2 --actor incident-automation --reason astropy-review

.venv/bin/python scripts/scientific_skill_runtime_control.py entry-enable \
  --entry-id skillentry_08923f34c517036a9ac1bfce65e1ba124034653d19d842edf2cecd5083b7c3ad \
  --expected-revision 3 --actor ops-automation --reason review-cleared
```

Use `--state /mounted/path/runtime-control.json` when the durable Hub volume uses
a non-default path. A stale revision returns exit code `2` with
`scientific_skill_runtime_control_revision_conflict`; read state and reassess
instead of blindly retrying.

## Upgrade checklist

1. Keep the current runtime state and approved catalog revision active.
2. Inventory the candidate immutable upstream pin without executing package code.
3. Run manifest/hash/license validation, deterministic risk profiling, capability
   diff, negative policy tests, adapter conformance, and sandbox gates.
4. Stop if scripts, network targets, credential requirements, or rights expand.
5. Verify the review receipt digest and the complete old/new visible diff.
6. Publish a new append-only catalog revision; never edit a stored revision.
7. Enable a bounded cohort through runtime control, observe alerts, then expand.
8. Retain old catalogs and all provenance receipts for audit and rollback.

## Pin rollback

Rollback is a new catalog publication, not mutation of the current file. Hub ops
automation calls `ScientificSkillCatalogRollbackService.rollback` with:

- current catalog ID and expected current digest (CAS fence);
- the stored historical catalog version containing the approved target pin;
- a new, unused catalog version;
- the selected skill name;
- hash-bound manifest/risk-profile bindings for every entry in the new revision.

The service restores the exact historical entry, validates every binding, and
appends the result. It rejects stale current digests, missing historical versions,
unapproved target pins, duplicate versions, and incomplete bindings. It has no
ports to Source, Knowledge, Qdrant, or CodeCompass persistence, so rollback cannot
overwrite those systems. After publication, keep the affected entry disabled
until the new revision has passed smoke verification, then re-enable it through
the CAS command above.

## Incident procedure

1. Run `show` and record revision/digest in the incident.
2. Use `entry-disable` for a single suspected skill; use `global-disable` for
   catalog, audit, authorization, or supply-chain uncertainty.
3. Confirm new selection attempts return
   `scientific_skill_pilot_runtime_disabled` or feature-disabled.
4. Preserve the runtime state, catalog revisions, audit events, and provenance
   receipts. Do not delete or rewrite evidence.
5. Determine whether re-enable, pin rollback, or a new reviewed upstream pin is
   appropriate.

## Alerts and ownership

Page the security/on-call owner for runtime-control digest/read failures,
`scientific_skill_pilot_runtime_control_unavailable`, approval/hash mismatches,
or unexpected capability expansion. Page the platform owner for repeated CAS
conflicts, catalog-store failures, and audit unavailability. Notify the research
governance owner for changes in domain risk, source terms, or data classification.

Alert on any selection after a disable command, any unverified provenance receipt,
and sustained increases in `not_admitted`, access-denied, or audit-required
outcomes. Security owns emergency disable and compromise decisions; platform owns
catalog publication and storage; research governance owns skill admission. No
worker may change these controls or delegate work to another worker.
