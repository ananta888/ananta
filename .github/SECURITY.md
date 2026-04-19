# Security Policy

## Supported Versions

Security fixes are prioritized for the current mainline and the latest tagged release candidate or stable release.

## Reporting a Vulnerability

Do not report suspected vulnerabilities through public issues.

Use GitHub private vulnerability reporting if it is enabled for this repository. If it is not enabled yet, contact the repository maintainers through a private channel and include:

- affected version, commit, or deployment profile
- clear reproduction steps
- expected impact
- relevant logs, requests, screenshots, or proof of concept
- whether the report affects authentication, secrets, CI/CD, container images, worker execution, or hub governance

## Handling Expectations

Maintainers should acknowledge security reports promptly, triage severity, and avoid public disclosure until a fix or mitigation path is available. Security-sensitive changes must go through review and should include regression coverage or a clear verification note.

## Scope

Security-sensitive areas include:

- authentication and authorization
- hub-worker task orchestration and policy enforcement
- worker execution boundaries
- secrets, GitHub Actions, release automation, Dockerfiles, and Compose files
- artifact handling, repository input parsing, terminal output, and generated content rendering
- OpenAI-compatible, MCP, webhook, and remote hub exposure surfaces
