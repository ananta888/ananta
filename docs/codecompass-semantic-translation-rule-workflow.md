# CodeCompass Semantic Translation — Rule Proposal and Promotion Workflow

This document defines the lifecycle of an equivalence rule from initial proposal to stable production use.

## Overview

LLMs may propose new equivalence rules, but they do not write directly to the stable registry. Every rule starts as `experimental`, must pass golden tests and explicit review before promotion.

```
LLM / human proposes rule
        ↓
Rule created as experimental=true in equivalence_rules.v1.json
        ↓
Schema validation (EquivalenceRuleRegistry.validate())
        ↓
Golden test added and passes deterministically
        ↓
Reviewer checks: preconditions, postconditions, known deviations
        ↓
Promotion: experimental=false, status="stable"
```

## Step 1: Proposal

An LLM or developer identifies a mapping pattern and proposes a new rule entry. The rule must specify:

- `rule_id`: unique, versioned, e.g. `eq.java_localdate.ts_iso_string.v1`
- `scope`: domain context (`dto`, `enum`, `expression`, `control_flow`)
- `source_language` / `target_language`
- `semantic_kind`: one of the recognised SEMANTIC_KINDS
- `preconditions`: list of conditions that must hold for safe transformation
- `postconditions`: list of observable properties the target output must satisfy
- `examples`: at least one source/target pair
- `tests`: at least one golden test reference

Proposed rules are added to `equivalence_rules.v1.json` with `"experimental": true`.

## Step 2: Schema Validation

`EquivalenceRuleRegistry.validate()` is called on load and enforces:

- No duplicate `rule_id`
- Every rule has at least one test reference
- `experimental=true` rules cannot have `status="stable"`
- No self-contradicting preconditions/postconditions

Any violation raises `ValueError` and prevents the registry from loading.

## Step 3: Golden Test

A golden test must be added to `tests/fixtures/semantic_translation_golden_samples.json` or a dedicated expression/rule test file. The test must:

- Be deterministic (no randomness, no network)
- Assert exact or AST-normalised output for the proposed rule
- Assert expected warnings are present (e.g. `date_format_contract_required`)

The test must pass in CI before promotion is allowed.

## Step 4: Review

A human or authorised agent reviews:

1. Are all preconditions reachable and checkable by the Transform Engine?
2. Are postconditions verifiable by the Verifier Service?
3. Are known deviations documented and acceptable?
4. Are there no high-risk warnings left unresolved?
5. Is the rule scoped correctly (not over-broad, not too narrow)?

The review is recorded in the rule's `known_deviations` and commit message.

## Step 5: Promotion

Promotion means setting `"experimental": false` and `"status": "stable"` in `equivalence_rules.v1.json`. This change must go through normal code review.

Rules may not be promoted to stable automatically by agents. Agents may create experimental rules and propose promotion, but a human must approve the final merge.

## Trace and Provenance

Every transform artifact records which rule IDs were applied (`rule_ids` field). The verifier records which rules were verified (`verified_rule_ids`). This makes it possible to audit which rules produced which output.

The trace indicates whether a rule was:
- `manually_authored`: created directly by a developer
- `llm_proposed`: proposed by an LLM, reviewed by a human
- `heuristic`: derived from statistical analysis of golden samples

This field is optional and informational — it does not affect rule behaviour.

## Experimental Rules in Production

Rules with `experimental=true` are excluded from `EquivalenceRuleRegistry.records()` by default and from `find()` queries. They are never applied by the Transform Engine unless explicitly opted in via `allowed_rule_ids`.

This ensures experimental rules cannot silently affect production translations.
