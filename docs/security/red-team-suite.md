# Red-Team Suite

## Scope

This suite validates harness self-modification rejection and adjacent injection/lifecycle regressions.

## Included tests

- Unknown evidence rejection
- Model revision rejection
- Scope/resource rejection
- Provider schema injection (non-dict, non-string id)
- Provider ID injection (`//`, `.py`)
- Symlink injection in provider schema
- No-activation lifecycle: symlink and executable self-modification targets rejected; plain files allowed

## Remaining matrix

Full admitted production benchmark tampering, reward-hacking, and OS/container escape regression matrix are tracked separately and not covered by this file.
