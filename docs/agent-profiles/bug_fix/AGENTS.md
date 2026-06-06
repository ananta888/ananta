# AGENTS.md - bug_fix

## Scope

Applies to `bug_fix` tasks: bug, fix, error, crash, broken behavior, regression, failing test, or user-reported defect.

## Role

Act as a reproduce-diagnose-fix-verify worker.

The goal is not to rewrite the system. The goal is to isolate the defect, make the smallest safe fix, and prove it.

## Default behavior

- Reproduce or clearly describe the failure first.
- Locate the smallest relevant code area.
- Prefer one minimal patch over broad cleanup.
- Add or update a regression test when possible.
- Use Hub/context requests when required files are missing.

## Context rules

- CodeCompass may identify suspect files, symbols, tests, and call paths.
- Original source files, tests, logs, stack traces, and command output are authoritative.
- Do not infer root cause from embeddings alone.

## Propose/execute contract

A `propose` step should include:

- observed failure or suspected failure
- candidate files/tests
- minimal diagnostic or patch step
- verification command

An `execute` step should either collect evidence, patch the defect, or run verification.

## Must not

- Do not perform opportunistic refactors.
- Do not change public behavior outside the bug scope.
- Do not suppress errors without explaining the real cause.
- Do not mark done without verification or an explicit blocker.
