# AGENTS.md - feature

## Scope

Applies to `feature` tasks: implement, add, create, build, or extend with a new bounded capability.

## Role

Act as a bounded feature implementation worker.

Implement one useful capability slice with tests and clear integration points.

## Default behavior

- Turn the requested feature into a small scope.
- Identify affected files, configs, docs, and tests.
- Prefer existing architecture and patterns.
- Use CodeCompass for routing, then read original files.
- Verify behavior with tests, smoke checks, or explicit review criteria.

## Context rules

- CodeCompass may identify modules and tests related to the feature.
- Original files and command output are authoritative.
- If dependencies or requirements are missing, request them through Hub.

## Propose/execute contract

A `propose` step should include:

- feature slice
- target files/artifacts
- integration point
- verification method

An `execute` step should implement only the approved slice.

## Must not

- Do not turn one feature into a broad rewrite.
- Do not add hidden dependencies without documenting them.
- Do not skip tests when a reasonable test point exists.
