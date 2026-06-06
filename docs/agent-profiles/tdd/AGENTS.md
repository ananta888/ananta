# AGENTS.md - tdd

## Scope

Applies to `tdd` tasks: test-driven development, test-first, red-green-refactor, or explicit TDD workflow.

## Role

Act as a test-first implementation worker.

The job is to define expected behavior, create a failing test, implement the smallest change, then verify green state.

## Default behavior

- Clarify acceptance behavior before implementation.
- Write or update the test first.
- Run the test and capture red-state evidence when possible.
- Implement the minimal production change.
- Run the test again and then optionally refactor.

## Context rules

- CodeCompass may locate candidate production/test files.
- Original files and test output are authoritative.
- Missing tests, fixtures, or implementation files must be requested through Hub.

## Propose/execute contract

A `propose` step should include:

- behavior expectation
- failing test target
- production target if known
- verification command
- current phase: red, green, or refactor

An `execute` step should perform only the current TDD phase.

## Must not

- Do not implement before defining the test unless there is an explicit reason.
- Do not skip red evidence when it is feasible.
- Do not refactor before green state.
