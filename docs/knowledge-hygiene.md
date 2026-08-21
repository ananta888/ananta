# Knowledge Hygiene

Knowledge Hygiene turns admitted project sources into a revision-safe, curated knowledge view without silently rewriting source truth.

## What users see

The project page at /knowledge-hygiene/:projectId provides:

- a health board that distinguishes complete zero from partial or unknown observations;
- a conflict inbox with both source-bound claims, evidence, state and timeline;
- digest-bound human choices with rationale and optional qualifiers;
- a read-only curated wiki browser with revisions, source references and visible warnings;
- links used by CodeCompass conflict and curated-wiki graph markers.

Markdown is displayed as escaped source in the browser. Generated Markdown files are useful for review and Obsidian consumption but can always be rebuilt from Hub records.

## Developer entry points

- ananta_contracts/knowledge_hygiene.py: immutable shared contracts and canonical digests.
- ananta_contracts/knowledge_claim_precedence.py: generic non-truth-selecting comparison.
- agent/services/knowledge_hygiene/: Hub application, analysis, run, projection and writeback services.
- agent/repositories/knowledge_hygiene_repository.py: scoped persistence port and adapters.
- worker/knowledge_hygiene/: pure proposal handlers.
- agent/routes/knowledge_hygiene.py: thin authenticated HTTP boundary.
- schemas/knowledge_hygiene/: strict versioned wire schemas.

Extend providers through SemanticSimilarityPort or KnowledgeWritebackPort. Do not add model calls, persistence or task delegation to worker handlers. Do not use curated wiki as an authoritative negative when the existing RIG coverage is partial or unknown.

## Privacy and retention

Audit records contain identifiers, hashes, counts and reason codes, not source bodies or proposed content. Canonical records are retained with project scope according to the Hub database policy. Generated Markdown and runtime benchmark output may be deleted and rebuilt.
