# ADR: avoid-ai-writing source strategy

Status: accepted

Use a pinned external read-only bundle rather than a git submodule, runtime
clone, or Python reimplementation. This keeps licensing and updates explicit,
avoids a Node dependency in the core container, supports offline CI through
contract fixtures/FakeSandbox and prevents silent upstream drift.

The bundle must contain the audited MIT license and detector file at the
documented commit/checksum. A mismatch disables only this provider. The core
scanner remains available. Network access and edit-in-place are not permitted.
