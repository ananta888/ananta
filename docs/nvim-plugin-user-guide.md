# Ananta Neovim Plugin User Guide

## Purpose

Use the Neovim plugin for developer-centric workflows: analyze, review, patch planning, and goal submission.

## Setup

1. Configure a connection profile (`endpoint`, `environment`, `auth_mode`).
2. Authenticate with your supported Ananta auth flow.
3. Run a first useful command (`AnantaAnalyze` or `AnantaReview`).

## Core commands

- `AnantaGoalSubmit`: submit a goal from current editor context.
- `AnantaAnalyze`: analyze current file or project context.
- `AnantaReview`: review selected code or current block.
- `AnantaPatchPlan`: request patch planning suggestions (no silent apply).
- `AnantaProjectNew`: start a new project path.
- `AnantaProjectEvolve`: evolve an existing project path.

## Daily workflow

1. Inspect context panel before submission.
2. Submit analyze/review/goal command.
3. Read results in editor-native views (scratch/floating/quick list).
4. Open deeper details in browser when necessary.

## Safety

- No hidden file mutation.
- Explicit approval awareness for risky actions.
- Bounded context submission with user visibility.
