# Changelog

This project uses GitHub Releases as the authoritative release history.

`CHANGELOG.md` is a curated index for notable stable releases, migration notes and operator-facing changes. It should not duplicate every pull request. Release candidates may rely on generated GitHub release notes plus the release verification assets.

## Unreleased

- GitHub release governance, release assets, PR templates, issue templates, security policy and AI-assisted review guidance are being prepared.

## Release Note Rules

- Stable releases use GitHub Releases as the primary public changelog.
- This file records only high-signal release summaries, migration notes, security notes and compatibility changes.
- PRs that affect runtime behavior, APIs, security posture, CI, Docker, release assets, config or operator workflows should opt into release notes in the pull request template.
- Trivial internal refactors, local-only test maintenance and typo-only changes do not need a changelog entry unless they alter user or operator expectations.
