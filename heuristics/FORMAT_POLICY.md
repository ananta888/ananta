# Heuristic Format Policy

## Canonical Format

**JSON is the canonical format.** All active heuristics live as `<heuristic_id>.heuristic.json` under `heuristics/active/`.

YAML is an optional authoring format in `heuristics/authoring/`. YAML must be normalized to JSON before any activation gate check. YAML files never become active directly.

## Folder Structure

```
heuristics/
  active/           # Active heuristics — runtime source of truth
  archive/          # Previous versions (not loaded at runtime)
  candidates/       # Proposals awaiting review
  quarantine/       # Suspended heuristics
  rejected/         # Permanently rejected proposals
  authoring/        # YAML drafts (authoring only, not loaded at runtime)
  schemas/          # JSON Schemas for validation
  index.json        # Registry index (heuristic_id, version, domain, status, file, runtime_mode)
```

## Naming Convention

- Active heuristics: `<heuristic_id>.heuristic.json` (snake_case ID)
- Proposals: `<proposal_id>.heuristic_proposal.json`
- Archive files: `<heuristic_id>-<version>.json`
- YAML drafts: `<heuristic_id>.heuristic.yaml`

## Runtime Mode Taxonomy

| mode | description |
|------|-------------|
| `declarative_rules` | Pure JSON DSL: triggers → selection → action. No code. |
| `python_strategy` | Allowlisted Python class in `agent/heuristics/strategies/`. Module + class must be in `_STRATEGY_ALLOWLIST`. |
| `composite_chain` | Ordered list of `heuristic_refs` evaluated as a chain. |

## Safety Classes

| class | allowed for |
|-------|-------------|
| `ui_motion_only` | Snake motion only. No context read beyond position. |
| `readonly` | Read local context, no writes. |
| `bounded` | Read + write local notes + send_to_chat. |
| `elevated` | Extended capabilities. Requires explicit justification. |

## Bootstrap Rules

1. All bootstrap heuristics use `deterministic: true`.
2. Snake heuristics must use `safety_class: ui_motion_only` or `readonly`.
3. No bootstrap heuristic may auto-activate new heuristics.
4. Chat heuristics must return `no_good_match` if score is below threshold — no invented content.
5. No bootstrap heuristic accesses secrets, performs file writes, or requests context extension.

## Auto-Activation Policy

**New heuristics are never activated automatically.**

The activation gate requires:
1. Validation passed
2. Simulation passed
3. Human approval registered in audit log

## OpenCode Flow for Python Strategies

When OpenCode proposes a new Python strategy or modifies an existing one, the following flow applies:

### Authoring flow
```
OpenCode draft (YAML or JSON)
  └─ HeuristicYamlImporter.import_file()      ← normalizes to candidate JSON
       └─ HeuristicFormatValidator.validate()  ← internal consistency checks
            └─ AiProposalGuardrails.check()    ← no invented refs/symbols
                 └─ HeuristicCatalogValidator  ← schema + safety class
                      └─ written to heuristics/candidates/
```

### Routing constraints for OpenCode
1. **Module allowlist** — Every Python strategy module + class must be added to
   `PythonStrategyLoader._STRATEGY_ALLOWLIST` by a human before activation.
   OpenCode cannot self-register new strategy classes at runtime.

2. **No inline code** — Python strategy code must live in `agent/heuristics/strategies/`.
   Inline code strings in JSON/YAML are forbidden and rejected by `PythonStrategyLoader`.

3. **Candidate-only writes** — OpenCode may only write to `heuristics/candidates/` or
   `heuristics/authoring/`. Writes to `heuristics/active/` require human approval via
   `HeuristicActivationGate.activate()`.

4. **Provenance required** — All OpenCode-authored proposals must include
   `provenance.created_by = "ananta-worker"` and a non-empty `description`.

5. **Anti-hallucination guard** — `AiProposalGuardrails` rejects proposals that reference
   file paths not found in the allowlist, class names not in `_STRATEGY_ALLOWLIST`,
   or claim `status: active` in YAML source.

### Routing guard interface
```python
from agent.services.heuristic_runtime.ai_proposal_guardrails import AiProposalGuardrails
guard = AiProposalGuardrails()
result = guard.check(proposal_dict)
if not result.passed:
    raise ValueError(result.rejection_reasons)
```
