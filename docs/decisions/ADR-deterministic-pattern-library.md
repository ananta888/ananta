# ADR: Deterministic Pattern Library for Code Templates

- **Status:** Accepted
- **Date:** 2026-06-09
- **Scope:** Pattern-guided code generation for workers — behavioral, structural, and creational GoF patterns

## Context

Workers produce code artifacts, but the quality and structural consistency of
that code depended entirely on the LLM's free-form generation.  This created
several problems:

1. **Non-reproducibility.** Two identical task descriptions could yield structurally
   different code, making automated structural testing impossible.
2. **No guardrails.** LLMs could introduce ad-hoc patterns, incorrect abstractions,
   or risky singletons without any validation layer.
3. **No audit trail.** There was no record linking a generated file to the pattern
   intent, catalog version, or render parameters that produced it.
4. **Inconsistency across languages.** Java, Python, and TypeScript workers had
   no shared pattern vocabulary.

## Decision

Introduce a **deterministic pattern library** that sits between the planning layer
and the code-generating worker:

1. **Pattern Registry** (`schemas/patterns/pattern_catalog.v1.json`) is the single
   source of truth for allowed patterns.  An LLM may only select a pattern from
   this list — it may not invent pattern definitions.
2. **PatternPlan** is the only interchange format.  It is validated locally
   against the registry before being handed to a renderer.  An LLM-generated
   PatternPlan that does not pass local validation is rejected; the worker
   receives a `blocked` response, not a silent incorrect plan.
3. **PatternTemplateRenderer** renders source code and test files from validated
   plans and pre-defined template files.  The renderer uses `string.Template`
   with `@@var@@` markers — no expression engine, no attribute access, no shell
   — so the same plan always produces byte-identical output.
4. **PatternGateService** runs structural checks on the rendered output
   (interface/protocol present, context class, ≥2 concrete implementations,
   test file) to confirm the generated code fulfils the pattern's contract.
5. **PatternArtifactService** records content-addressed render manifests with
   per-file sha256 hashes.  The `plan_hash` is deterministic so idempotent
   re-renders do not create duplicate records.
6. **Blueprint workflow steps** may carry optional `pattern_hints`
   (`allowed_patterns`, `preferred_patterns`, `forbid_patterns`,
   `language_targets`, `require_tests`) that narrow what a worker may propose.
   Hints are validated at catalog-seed time and propagated via
   `worker_execution_context` to the workspace context files.

## LLM Role

The LLM's role is strictly **selection and parametrization**, never definition:

| Allowed | Forbidden |
|---------|-----------|
| Pick a `pattern_id` from the registry | Define a new pattern schema |
| Provide `parameters` (e.g. `context_class: "Order"`) | Override template file paths |
| Choose `language` from `java/python/typescript` | Extend the registry at runtime |
| Provide `selection_reason` | Use a pattern outside the allowed list |

This boundary is enforced in code:
- `PatternProposalNormalizer` strips unknown keys and rejects unknown IDs.
- `PatternSelectionPolicy` applies per-task-kind allowlists; singleton-guarded patterns
  require explicit policy override.
- `PatternPlanService` validates required roles, parameter types, and output path safety.

## Security Considerations

- **Path traversal**: Template paths are allowlisted to `config/patterns/templates/`.
  The renderer rejects any output path containing `..` or starting with `/`.
- **Template injection**: The renderer uses `string.Template`, not Jinja2.
  Expression evaluation and attribute access are impossible.
- **Singleton risk**: `singleton_guarded` patterns carry `risk_level: high` and are
  blocked by `PatternSelectionPolicy` unless `allow_risky_patterns: true` is set
  explicitly in a per-blueprint policy override.
- **Least privilege**: The renderer never opens network connections or spawns
  subprocesses.  It writes only inside the declared `target_root`.

## Alternatives Considered

| Alternative | Reason rejected |
|-------------|-----------------|
| Free-form LLM code generation only | Non-deterministic, no structural audit trail |
| Jinja2 templates | Expression evaluation creates injection surface; harder to sandbox |
| Single global pattern schema per language | Too rigid; patterns must evolve independently |
| Runtime pattern registration from worker output | Workers cannot be trusted to define schemas |

## Consequences

**Positive:**
- Byte-deterministic code generation enables snapshot/golden-file tests.
- Every generated file is traceable back to a `pattern_id`, `catalog_version`, and `plan_hash`.
- Structural gates can be added per pattern without changing worker prompts.
- New patterns are added by committing catalog JSON + template files (no code change needed).

**Negative/Trade-offs:**
- Patterns not in the catalog are unavailable until they are added and reviewed.
- Template files must be maintained alongside code; a broken template breaks all renders for that pattern.
- The `@@var@@` substitution is less expressive than Jinja2; conditional blocks require multiple templates.

## Related Documents

- `docs/patterns/README.md` — usage guide and examples
- `schemas/patterns/pattern_catalog.v1.json` — live pattern registry
- `scripts/validate_pattern_catalog.py` — catalog validation CLI
- `agent/services/pattern_template_renderer.py` — renderer implementation
- `agent/services/pattern_gate_service.py` — structural gate checks
- `agent/services/pattern_artifact_service.py` — artifact recording
