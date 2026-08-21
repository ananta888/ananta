# Knowledge Hygiene architecture

Status: accepted  
Contract version: knowledge_hygiene.v1  
Precedence version: knowledge_claim_precedence.v1

## Baseline and gap analysis

Ananta already had a Hub-owned KnowledgeIndex, exact retrieval bindings, SourceCatalog identities, citations, MemoryTree ingestion, Obsidian export, human approvals, CodeCompass supplements and coverage-aware RIG precedence. It did not have a versioned generic claim ledger, deterministic conflict lifecycle, curated-wiki revisions or a controlled source-correction path.

Knowledge Hygiene fills only those gaps. It does not create another RAG pipeline, source registry, approval system, graph truth model or worker scheduler.

## ADR: canonical truth and authority

Decision: accepted.

1. Original admitted source revisions remain primary evidence.
2. The Hub-owned append-only Claim ledger is the canonical normalized analysis record.
3. Conflict decisions and correction approvals are separate append-only human acts.
4. CuratedWikiPage records are canonical page revisions, but generated Markdown files are disposable projections.
5. CodeCompass graph data is a versioned supplement, not a replacement graph.
6. Curated-wiki retrieval has supplement-only authority and cannot override a complete RIG result.
7. A worker can propose records but cannot persist, decide, dispatch another worker or advance a workflow phase.

Consequences:

- Deleting the generated Markdown directory loses no canonical state.
- A source correction does not resolve a conflict until a new exact source revision is ingested with complete coverage and rechecked.
- Partial or unknown coverage is displayed as observed lower bounds, never as a trustworthy zero.
- Trust and freshness rank review work but never select truth.

## Hub-worker flow

1. The Hub admits existing SRC_#### or RUN_#### identifiers and exact revisions through the existing source/index boundary.
2. The Hub creates KnowledgeHygieneRun with immutable bindings, policy/profile versions, budgets, actor and assignment digest.
3. A worker receives an expiring lease and allowed operations.
4. The worker validates source, revision, locator and content hash, then returns untrusted proposals plus a result digest.
5. The Hub rehydrates canonical claims, validates every binding, applies idempotency, and records the result.
6. Deterministic bucketed analysis creates candidates; semantic similarity is an optional injected port and can only propose.
7. The Hub persists conflicts, pages, graph supplements, health snapshots and redacted audit events.
8. A human makes a digest- and version-bound decision.
9. Optional correction and writeback use a separate approval and adapter capability.
10. Only a subsequent complete re-ingest can close a correction-dependent conflict.

## Responsibility boundaries and SOLID check

- SRP: contracts, SQL models, repository adapters, pure analysis, run state, projections, writeback, HTTP and UI live in separate modules.
- OCP: similarity and writeback are narrow ports; new providers do not modify conflict policy or Hub orchestration.
- LSP: InMemoryKnowledgeHygieneRepository mirrors SQL scoping, idempotency and CAS behavior for deterministic tests.
- ISP: workers receive task-specific handlers rather than a Hub service or broad repository interface.
- DIP: KnowledgeHygieneService depends on repository and writeback abstractions, not concrete storage or vault logic.

No worker-to-worker path, implicit cross-container state or automatic truth decision was introduced.

## Data lifecycle

Claims are append-only by claim identity and revision. The idempotency key includes project, source identifier, exact source revision, locator and normalized payload. A retry returns the first record; a changed payload or source revision creates the next revision.

Conflicts use an order-independent pair key and a CAS version. Human decisions persist atomically with the state transition. Automated recheck has its own CAS path and audit event; it cannot manufacture a human decision.

Wiki pages are revisioned by project and slug. Each page carries exact claim refs, original source refs, conflict warnings, aliases, coverage and content hash. The Hub rejects a proposal that omits a relevant open warning.

## Existing integrations

- KnowledgeIndexRetrievalService remains responsible for exact bound-record rehydration.
- retrieval_source_contract adds curated_wiki as an optional source type.
- CuratedWikiRetrievalAdapter exposes page, claim, source, conflict, revision and coverage metadata.
- The existing RIG truth precedence remains unchanged; curated wiki is supplement_only.
- GraphSupplement nodes and edges carry basis and supplement hashes for Hub admission.
- Obsidian is the only MVP writeback adapter. Git and external sources are intentionally unsupported.

## Known preserved constraints

The existing service-locator composition style remains in use for Flask routes. This is a pre-existing global-composition compromise relative to DIP; domain services themselves remain constructor-injected and testable. Replacing the application-wide composition model is outside this additive track.
