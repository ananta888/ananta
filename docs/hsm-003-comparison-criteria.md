# HSM-003 Comparison Criteria

## Purpose
Define stable, testable criteria used to compare candidate decisions in the HSM-003 workflow.

## Criteria
1. **Correctness**
   - The decision must satisfy all explicit task constraints.
   - It must not introduce policy violations or out-of-scope changes.

2. **Scope Control**
   - Only declared files may be modified.
   - No commits, pushes, task creation, policy changes, Git metadata edits, or worker contact are allowed.

3. **Reproducibility**
   - The decision must be verifiable by running the exact allowlisted test command.
   - Success is defined as exit code 0 from `python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development`.

4. **Minimal Change**
   - Prefer the smallest diff that satisfies correctness and scope control.
   - Avoid unrelated refactors, formatting changes, or metadata updates.

5. **Evidence-Based Completion**
   - A decision is complete only when the Hub confirms the required test result with exit code 0.
   - No completion claim may be made without confirmed evidence.

## Non-Criteria
- Aesthetic preferences are not comparison criteria unless they affect correctness or scope control.
- Performance claims are not considered unless explicitly required by the task brief.
- Publication, commit, or push behavior is handled by the Hub and is outside this decision's scope.
