# Source research and acceptance review in self-development

The Hub owns admission, orchestration, evidence and publication. Workers execute
individual assignments in their containers. This change adds prerequisites to
the existing path; it does not introduce another scheduler.

## Requirements and source context

TODO ingestion preserves acceptance criteria, references, task type, task goal,
document goal and purpose. Requirements that cannot be forwarded within 64 KiB
are blocked instead of truncated. Existing journal entries are not retroactively
reconstructed or promoted; re-admit corrected source requirements before a new
attempt.

Contract planning and execution receive a bounded source bundle from the exact
checkout revision. It includes actual snippets, paths, line offsets, full-file
SHA-256 hashes and truncation markers. Explicit references come first; task and
goal terms rank related paths across the tracked catalog. The limits are eight
files, 4,000 characters per file, 20,000 characters overall and 64 read candidates.
Dirty bytes, symlinks, protected paths and unavailable files are reported as
unavailable. This is an initial source observation, not a complete caller graph
or a claim that an entire goal has been researched. Workers must continue
authorized CodeCompass inspection when the initial context is insufficient.

## Mandatory analysis before writing

An operator can opt in with this private Hub policy block:

```json
{
  "quality_gates": {
    "require_code_analysis": true,
    "require_acceptance_review": true
  }
}
```

The first flag enables `require_code_analysis` in the shared Worker mutation
configuration. It therefore applies to other tasks using that mutation runtime
as well. Existing policy without the flags retains its previous behavior.
Reload the Hub and participating Workers after changing runtime composition.

With mandatory analysis, the existing mutation loop requires successful
CodeCompass inspection, actual materialized source reads and an explicit
`code_analysis` action citing observed paths, hashes and line ranges, with
findings, change reasoning and test reasoning. All three mutation paths
(`workspace_write`, `patch_request`, and mutating tool requests) enforce the
same prerequisite. Read nudges cannot force blind writes before analysis.
Iteration budgets still bound failure. Setup errors cannot fall back to a
weaker executor when analysis is mandatory.

The Hub separately checks the report's Task identity and source-range bindings
against the original exported bytes. Its admitted execution result binds the
analysis digest to the pre-reserved Hub run. A missing or mismatched report
blocks publication even if the Worker configuration did not enforce the gate.

These checks prove that code was observed and an analysis was supplied. They
cannot prove the semantic correctness of the analysis. The mutation report is
a technical observation, not an independently issued `SRC_*` or `RUN_*` identity.

## Independent acceptance review

The second flag blocks start and dispatch unless a trusted Hub composition
provides `app.extensions['self_development_acceptance_reviewer']`, implementing
`AcceptanceReviewer`. No default model call is substituted for a reviewer.
Without the adapter, the API returns the bounded reason
`independent_acceptance_reviewer_unavailable`; it does not wait for a person.

The adapter must use the existing Hub task queue and role workflow to delegate a
separate review Task, reserve its Registry run before execution, and inspect the
immutable snapshot at the Hub's `sealed/<task_id>` boundary. The adapter exports
the snapshot through the ordinary Worker workspace boundary; it must not expose
the private control directory or credentials to a Worker. An eligible reviewer
is resolved by the Hub, not selected by model text.

The review run must bind `input_digest` to `AcceptanceReviewRequest.digest`, the
original repository revision, and `environment_digest` to the canonical digest
of `{"worker": reviewer_worker_url, "phase": "acceptance-review"}`. Its result
digest is the canonical digest of `dataclasses.asdict(AcceptanceReviewResult)`.
Approval requires every original criterion to pass and no unresolved findings.
The Hub verifies the registered assignment, dispatch lease, source inputs,
result, revision and local evidence scope. Unknown or synthetic runs cannot
satisfy the production verifier. The receipt is revalidated at publication.

The adapter must have a bounded timeout and return a failed or blocked result
when no eligible reviewer is available. Do not synchronously wait inside the
single-worker dispatch for a Task queued behind that same dispatch.

## Queue-delegated review workflow (phased, productive adapter)

`agent/services/self_development/review_workflow.py` supplies the production
adapter (`QueueAcceptanceReviewer`). The Hub composes it automatically when the
review gate is enabled and no explicit
`app.extensions['self_development_acceptance_reviewer']` adapter is present; the
explicit extension always wins. Reviewer resolution is Hub-owned: online Workers
distinct from the author URL win, review-capable roles first, ties by URL; with
no eligible Worker the development attempt fails bounded with
`independent_acceptance_reviewer_unavailable`.

The workflow is phased so the single development dispatch is never blocked by
the queue behind it:

1. **Development dispatch** (gate satisfied: CodeCompass, source reads,
   analysis) seals the change set, then admits the review: the adapter registers
   the review source, reserves the Registry run before execution
   (`input_digest = AcceptanceReviewRequest.digest`,
   `environment_digest = canonical({"worker": reviewer_url, "phase": "acceptance-review"})`)
   and ingests a separate `gate_review` Task with `review`/`analysis`
   capabilities and a sticky reviewer assignment. The item pauses in
   `pending_review`; nothing publishes.
2. **Review dispatch** (the review Task's own queue turn) exports the exact
   admitted revision (`git archive`, traversal-guarded, no Git metadata) plus
   the hash-verified sealed change set through the ordinary Worker workspace
   boundary, pins the closed assignment projection, and hands the bounded brief
   to the reviewer Worker. The Worker's final answer is persisted as
   `.ananta/last-final-answer.json` (worker-side artifact, never sealed);
   the Hub parses it strictly into `AcceptanceReviewResult`, records the
   Registry result and revalidates the complete receipt before publication.
3. **Publication** reuses the unchanged chain: completion sealing, verified
   publication, dependency release. `DevelopmentCoordinator._verify`
   revalidates the stored receipt exactly as before.

Failures fail closed and bounded: self-review binding mismatches, negative or
unparseable verdicts, missing or altered snapshots, wrong verification commands
and stopped sessions leave the item `failed` with
`acceptance_review_failed:<reason>` and never publish. A bounded retry
(`max_attempts`, operator-triggered or automatic after dispatch failures)
cancels the superseded review Task and starts a fresh attempt with a fresh
review. Unknown or synthetic runs cannot satisfy the production verifier; the
receipt is revalidated at publication.

Residual boundary: the reviewer Worker is identified by its URL; multiple
logical agents on one Worker are not independently identified. Supporting that
requires an authoritative logical-agent assignment binding, not merely a
different role label. Organization approval issuance by itself remains not
substantive code review.

No TODO becomes `done` under the opt-in policy until execution and independent
acceptance review succeed. A stopped service also suppresses automatic TODO
unblocking/archive publication and watchdog rearming. Historical `done` entries
are left untouched for a separate audit.

## Validation and responsibility boundaries

Run the isolated headless suite:

```sh
python -m pytest -q --confcutdir=tests/self_development -o addopts= -p no:cacheprovider tests/self_development tests/test_ananta_worker_workspace_feedback_iteration.py
```

Tests use explicitly synthetic Registry evidence and deterministic Worker
boundaries. These results are local technical observations, not release evidence.

SRP: requirement ingestion, source selection, mutation analysis and acceptance
verification have separate owners. DIP: the coordinator depends on a reviewer
port, not a concrete model backend. Existing coordinator orchestration/persistence
coupling and untyped legacy service interfaces remain architectural debt.
