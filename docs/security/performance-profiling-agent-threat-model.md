# Performance Profiling Agent Threat Model

Primary risks:

- benchmark manipulation and cherry-picking
- test overfitting
- resource exhaustion
- profiler logs leaking secrets
- unreviewed AI-generated patches
- destructive shell commands

Controls:

- commands route through CommandPolicy and NativeWorkerRuntime
- patches apply first in a temporary sandbox
- reports require baseline, candidate, comparison and regression evidence
- missing tests or output diffs produce blocked or inconclusive status
- large logs are referenced or truncated, not embedded wholesale
- network, sudo, host mutation and path escape remain default-deny
