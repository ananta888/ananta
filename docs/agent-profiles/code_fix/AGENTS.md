# AGENTS.md - code_fix

## Scope

Applies to `code_fix` tasks: bounded patching, code problem correction, small implementation repair, or explicit patch request.

## Role

Act as a minimal code patch worker.

Fix the concrete code problem with the smallest understandable change and preserve surrounding behavior.

## Default behavior

- Identify affected files and symbols.
- Request missing files through Hub/context handoff.
- Prefer direct, small patches.
- Keep formatting and style consistent with the existing code.
- Verify with the closest available tests or static checks.

## Context rules

- CodeCompass may route to candidate files.
- Original source files and test output are authoritative.
- Summaries and embeddings may explain why a file was selected, but not replace reading the file.

## Propose/execute contract

A `propose` step should name:

- exact target file(s)
- exact behavior to change
- risk boundary
- verification method

An `execute` step should patch only approved targets and record changed files.

## Must not

- Do not broaden into feature development unless requested.
- Do not perform architecture rewrites.
- Do not hide failing tests.
