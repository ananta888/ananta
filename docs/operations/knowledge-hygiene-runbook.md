# Knowledge Hygiene runbook

## Flags and rollout

All flags are fail-closed. The default is enabled=false, mode=disabled, auto_run_enabled=false and source_writeback_enabled=false.

Rollout order:

1. disabled: deploy schema and code with no analysis access.
2. observe: allow manual runs and collect candidates/health without decisions or writes.
3. manual: enable the conflict workbench and human decisions.
4. wiki read: expose curated Markdown, graph and retrieval supplements.
5. patch only: permit correction proposals but no adapter write.
6. controlled Obsidian writeback: configure explicit roots and enable the separate flag.

Rollback is immediate: set mode=disabled. Existing append-only records remain readable for audit; no worker is dispatched automatically. Disable source_writeback_enabled first if only writes must stop.

## Starting a run

Supply an unused RUN_#### ID and only existing verified SRC_####/RUN_#### source bindings. Each binding requires exact revision, SHA-256 and allowed locators. The Idempotency-Key header must equal the body run_id.

Dispatch assigns one worker and an expiring lease. A worker may checkpoint monotonically. Cancellation removes the lease. A partial, failed or cancelled run can be restarted only under a new provided run identifier.

## Interpreting results

- complete: all assigned evidence within configured budgets was evaluated; zero is meaningful.
- partial: some assigned evidence or candidates were skipped; observed values are lower bounds.
- unknown: completeness cannot be established; missing evidence is not a negative finding.

Deterministic metrics and LLM metrics are separate. Unmeasured token/provider/latency values remain null with a reason code; estimates are never labeled measured.

## Conflict response

Open the workbench and compare both exact claim revisions and source locators. Record rationale and qualifiers. A stale basis returns conflict and must be reloaded; do not bypass CAS.

For a source correction:

1. Record the human conflict decision.
2. Let a bound worker propose a patch against the exact source revision and hash.
3. Review the three-way base/current/proposed hashes and content diff.
4. Use the distinct writeback approval endpoint.
5. Re-ingest the resulting source as a new revision.
6. Run complete analysis and recheck the conflict against claims from that exact run.

The conflict must remain pending_reingest until step 6 succeeds. A recurring contradiction reopens it.

## Performance envelope

The release benchmark profile models at least 2,000 notes and 4,000 claims/files. Candidate generation is bucketed by project, scope, subject and predicate. Limits are configured for claims, candidate pairs, pages and patch bytes; budget exhaustion is a partial result rather than a silent success.

Target operator SLOs on the reference profile:

- deterministic 4,000-claim analysis below 10 seconds;
- 100-item API page below 500 ms excluding network;
- UI first health/conflict/wiki bundle below 2 seconds on a local Hub;
- bounded memory proportional to admitted claims plus configured candidate budget.

## Troubleshooting reason codes

- assignment_digest_mismatch: assignment fields changed after Hub issuance.
- claim_source_outside_assignment or claim_locator_outside_assignment: worker output escaped scope.
- candidate_budget_exhausted: increase the reviewed profile budget or split scope; coverage remains partial.
- stale_conflict_revision: reload both sides before deciding.
- source_revision_race: source changed after patch proposal; re-ingest and regenerate.
- source_writeback_disabled: expected until the final controlled rollout phase.
- complete_recheck_evidence_required: do not close from a partial run.

## Evidence and rollback record

The release gate writes artifacts/test-gates/knowledge-hygiene-release.json with schema, fixture, benchmark, security and configuration evidence. Runtime reports under artifacts root remain untracked; only the stable test-gate report is source-controlled.
