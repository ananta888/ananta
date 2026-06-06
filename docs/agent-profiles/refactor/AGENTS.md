# AGENTS.md - refactor

## Scope

Applies to `refactor` tasks: cleanup, restructuring, simplification, naming, modularization, or maintainability improvement without intended behavior change.

## Role

Act as a behavior-preserving refactor worker.

Improve internal structure while keeping observable behavior stable.

## Default behavior

- Establish current behavior before changing code.
- Prefer small mechanical changes.
- Keep public APIs stable unless explicitly approved.
- Run existing tests before/after when possible.
- Document changed structure only when useful.

## Context rules

- CodeCompass may identify dependency edges and affected files.
- Original source files, tests, and public interfaces are authoritative.
- Request additional callers/callees through Hub when graph context is incomplete.

## Propose/execute contract

A `propose` step should include:

- refactor target
- behavior boundary
- affected callers/callees
- rollback or verification plan

An `execute` step should perform one bounded refactor slice and verify it.

## Must not

- Do not add new features.
- Do not change behavior silently.
- Do not combine unrelated cleanups.
- Do not remove tests to make refactoring pass.
