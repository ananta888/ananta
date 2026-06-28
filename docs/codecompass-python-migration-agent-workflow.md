# CodeCompass: Python Migration Agent Workflow

Version: `v1`. Track: `codecompass-python-to-java-rust-translation`.

This document defines the step-by-step workflow an agent must follow when migrating Python code to Java or Rust via CodeCompass. The workflow is compatible with the Ananta Worker/Hub principle and Default-Deny tool grants.

---

## Overview

```
Step 1: Analyse
    ↓
Step 2: Translation Plan (no code written yet)
    ↓
Step 3: Rule Check + Blocker Review
    ↓
Step 4: Human Approval Gate  ← MANDATORY before any file changes
    ↓
Step 5: Code Generation + Verification
    ↓
Step 6: Trace Storage
```

The agent **must never write target files without Human Approval**. Blockers stop the pipeline; the agent reports them and waits.

---

## Step 1: Analyse

**Tool:** `codecompass.python_translation_plan` (or direct adapter call)

**What the agent does:**
1. Reads the Python source file(s) to be migrated.
2. Calls `codecompass.python_translation_plan` with `source_code`, `source_path`, and `target` (`java`, `rust`, or `both`).
3. Inspects the returned `plan`:
   - `dynamic_blockers` — if non-empty, stop and report (Step 3 handles this)
   - `entries[].status` — `safe_auto_transform`, `needs_review`, `blocked_dynamic_runtime`, `unsupported`
   - `entries[].type_confidence` — `annotated`, `inferred_from_default`, `unknown`, `dynamic`
   - `entries[].warnings` — inspect all warnings before proceeding

**Output:** A structured plan showing what can and cannot be transformed.

---

## Step 2: Rule Check + Blocker Review

**The agent must present to the human:**

1. **Blockers:** Any `blocked_dynamic_runtime` entries or `dynamic_blockers` in the plan.
   - Format: `[BLOCKER] {symbol}: {reason} at line {line}`
   - The agent must NOT proceed with file writes if any blockers exist for the target symbol.

2. **Needs Review:** Entries with `status=needs_review` or `type_confidence=unknown`.
   - The agent explains WHY review is needed (missing annotation, lossy numeric mapping, etc.)
   - The agent may propose a fix (e.g., "Add type annotation for parameter `x`").

3. **Applied Rules:** List all rule IDs that will be applied (e.g., `pyjr.dataclass_to_java_record.v1`).

4. **Warnings:** List all warnings — never suppress them silently.

**Trace entry at this step:**
```json
{
  "step": "rule_check",
  "plan_source_hash": "...",
  "blockers": [...],
  "needs_review": [...],
  "rules": [...],
  "warnings": [...]
}
```

---

## Step 3: Human Approval Gate

**This step is mandatory.** The agent must:

1. Present the translation plan summary to the human.
2. Show the planned target code (preview from `entries[].java_artifact.source` or `rust_artifact.source`).
3. Ask explicitly: "Shall I write these files? (Yes / No / Modify)"
4. Wait for a response — **do not proceed automatically**.

**The agent must NOT:**
- Write target files without explicit approval.
- Overwrite existing target files without showing a diff first.
- Skip this gate based on the presence of `safe_auto_transform` alone.

---

## Step 4: Code Generation + Verification

After human approval, the agent:

1. Calls the Transform Engine (via `codecompass.python_translation_plan` or `PythonTransformEngine`) to produce the final target artifacts.
2. Calls the Verifier (`PythonToJavaVerifier` or `PythonToRustVerifier`) to check:
   - Field completeness
   - Optionality preservation
   - Enum value completeness
   - Numeric precision warnings
3. Calls the `SemanticDiffEngine` to confirm no semantic divergence.

**Verifier status values:**
- `verified` — no issues found
- `verified_with_warnings` — warnings present, human should review
- `needs_review` — structural concerns, do not auto-write
- `failed` — blocking issues, do not write

**The agent must NOT write files if verifier status is `failed`.**

---

## Step 5: Trace Storage

Every transformation produces a `TransformArtifact` that must be stored:

```json
{
  "source_path": "src/models/user.py",
  "source_hash": "sha256:...",
  "target_hash": "sha256:...",
  "target_language": "java",
  "symbol": "User",
  "kind": "dataclass",
  "target_source": "public record User(...) {}",
  "rule_ids": ["pyjr.dataclass_to_java_record.v1"],
  "warnings": [],
  "verifier_status": "verified",
  "needs_review": false,
  "created_at": "2026-06-28T...",
  "ownership_decisions": []
}
```

The trace must be stored so that:
- Any future re-run can compare against the previous source hash.
- The human can audit which rules produced which output.
- Ownership and nullability decisions are documented.

---

## Compatibility with Ananta Worker/Hub

- The `codecompass.python_translation_plan` tool is registered in the standard tool dispatch table.
- File writes require the `repo.write_file` or `repo.apply_patch` tools — which have Default-Deny access control.
- Agents must request explicit file-write permission per target file.
- The workflow is intentionally multi-step so that no single LLM call can produce and write target code without human oversight.

---

## When NOT to run this workflow

- When the source Python file contains dynamic features (eval, exec, metaclasses) — report blockers and stop.
- When `type_confidence == "unknown"` for more than 50% of fields — ask the human to add type annotations first.
- When the target file already exists and has been manually edited — require explicit overwrite confirmation with diff.
- When the file is larger than the supported analysis window — split into smaller units first.

---

## Example Agent Trace

```
[PLAN] User.py → Java
  - User (dataclass): safe_auto_transform ✓
    - Rules: pyjr.dataclass_to_java_record.v1
    - Warnings: int_precision_policy for field 'age'
  - Status: ACTIVE (Enum): safe_auto_transform ✓
    - Rules: pyjr.enum_to_java_enum.v1

[APPROVAL REQUIRED]
Write src/main/java/com/example/User.java? (Yes/No)

[AFTER APPROVAL]
[GENERATE] User.java → verified ✓
[GENERATE] Status.java → verified ✓
[TRACE] Stored 2 artifacts for User.py
```
