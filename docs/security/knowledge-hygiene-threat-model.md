# Knowledge Hygiene threat model

## Assets

- original admitted source revisions and source identifiers;
- Hub-owned claims, conflicts, decisions, runs and audit history;
- curated wiki revisions and graph/retrieval projections;
- local Obsidian vault content eligible for controlled correction.

## Trust boundaries

The Hub, SQL database and authenticated human policy boundary are trusted control-plane components. Worker output, Markdown, model output, semantic scores, browser input and local source paths are untrusted.

## Threats and controls

| Threat | Control |
| --- | --- |
| Invented source identity | Contracts accept only provided SRC_#### and RUN_#### identifiers; Hub matches exact run bindings. |
| Worker expands scope | Assignment digest binds project, sources, locators, policy, profile and budgets; every result is revalidated by Hub. |
| Worker orchestrates follow-up work | Worker handlers expose pure handle methods and receive no queue, repository or worker client. |
| Replay with modified output | Assignment and canonical result digests; repository idempotency; conflicting replay fails closed. |
| Cross-project disclosure | Every repository query includes project scope; API rejects mismatched project headers and scoped auth claims. |
| Automatic truth selection | Precedence can only classify and order; only human decisions mutate lifecycle state. |
| False healthy zero | Canonical counts are null for partial/unknown coverage; observed lower bounds are separate. |
| Prompt/Markdown injection | Worker proposals are untrusted; Angular uses escaped interpolation and displays Markdown as text; generated files do not execute content. |
| Warning removal | Hub recomputes relevant conflict refs and rejects incomplete wiki proposals. |
| Stale human decision | CAS version and basis digest bind both exact claim revisions. |
| Unauthorized writeback | Default-off feature, manual mode, separate approval, injected capability and allowed roots. |
| Traversal or symlink escape | Strict existing-file resolution, extension allowlist, symlink rejection and resolved-root containment. |
| TOCTOU source overwrite | Per-target lock, hash recheck, bounded read, atomic backup and os.replace. |
| Secret leakage in audit | Payload key redaction and bounded structured metrics; source or proposed content is never logged. |
| Semantic-provider egress | Semantic analysis is optional behind an injected port and disabled by the deterministic default profile. Existing egress policy must authorize any provider adapter. |
| Resource exhaustion | Claims, pages, candidate pairs, patch bytes, API pages and worker lease duration are bounded. Exhaustion yields partial coverage. |

## Explicit exclusions

The MVP does not write to Git remotes, network wikis or arbitrary external systems. It does not delete source text, auto-merge claims, execute Markdown, create source IDs or alter the existing complete-RIG truth rule.

## Abuse-test expectations

Release tests cover unknown IDs, assignment tampering, locator/hash mismatch, expired leases, cross-project claims, replay divergence, stale CAS, non-human decisions, incomplete-coverage zeros, traversal, symlink escape, oversized content and source-race writeback.
