# Documentation drift decision matrix

Decision policy source: `todo.doc.json -> decision_policy`.

| Drift | Decision | Strategy tag | Rationale | Verification impact |
| --- | --- | --- | --- | --- |
| Bootstrap OpenAI example used `--base-url` while init parser supports `--endpoint-url` | update docs + installer outputs | update_both | user-impacting command error on first-run path | `tests/test_bootstrap_docs.py` validates endpoint flag contract |
| Goal CLI next-step output used module entrypoint | update code + snapshots/tests | update_both | default user guidance should follow `ananta ...` path | `tests/test_cli_goals.py`, `tests/test_cli_goals_shortcuts.py`, e2e snapshots |
| Demo flow examples defaulted to module entrypoint | update docs | docs_first | docs drift, runtime already supports user-path aliases | command contract test coverage in `tests/test_cli_docs_contract.py` |
| Onboarding mixed CLI-local and Docker-full-stack defaults | update docs | docs_first | confusion risk for new users; no runtime behavior change needed | docs contract and presence tests |
| Python minimum mismatch (`3.11+` docs vs runtime/tooling `3.10+`) | update docs to runtime baseline | docs_first | no silent runtime baseline raise in docs cleanup | reviewed in setup docs and init docs |
| Fallback vocabulary mismatch (`delegated|hub_fallback` vs runtime values) | update docs, keep runtime values | docs_first | terminology drift only; runtime fields are test-backed | `tests/test_tasks_autopilot.py` mapping assertion |
| CLI help dependency coupling | keep code unchanged, document precondition | defer code change / docs_first | no proven runtime benefit for refactor yet | documented in `docs/cli/commands.md` |
| Release gate missing docs-drift hook | add optional report/strict mode | update_both | prevent recurring drift without breaking default gate path | `tests/test_release_gate_docs_drift.py` |
