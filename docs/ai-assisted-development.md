# AI-Assisted Development Policy

Ananta allows AI-assisted development, but generated code is not trusted by default.

The hub-worker architecture, security boundaries and release process remain human-governed. AI tools can help draft changes, tests, docs and analysis, but maintainers remain responsible for design, review and verification.

## Allowed Use

AI assistance is appropriate for:

- drafting tests, documentation and examples
- summarizing code paths or release evidence
- proposing small refactors that preserve existing architecture
- generating repetitive scaffolding that a maintainer reviews
- exploring implementation options before a human chooses the final structure

## High-Risk Areas

Changes in these areas require stronger human review when AI assistance was used:

- authentication, authorization and token handling
- hub orchestration, task state transitions and worker delegation
- tool permissions, terminal access, MCP, OpenAI-compatible exposure and remote hub federation
- artifact parsing, repository input handling and generated-content rendering
- GitHub Actions, Dockerfiles, release scripts, dependency locks and publish workflows
- secrets handling, security policy, branch protection and repository governance

## Review Standard

For AI-assisted changes in high-risk areas:

1. State AI assistance in the PR template.
2. Explain the main design choice in human terms.
3. Confirm compatibility with hub-controlled orchestration.
4. Confirm no worker-to-worker orchestration path was introduced.
5. List the checks or tests run, or state why they were not run.
6. Request review from the relevant owner when CODEOWNERS applies.

## Non-Negotiable Boundaries

- AI output does not override project architecture.
- AI output does not count as security review.
- AI output does not justify bypassing tests, release gates or required reviews.
- Generated code must remain maintainable, small enough to review and consistent with SOLID principles.
- Any uncertainty about security, secrets or release impact must be surfaced in the PR.
