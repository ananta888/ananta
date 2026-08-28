# Coding-agent providers

Ananta integrates coding agents through the Hub-owned `CodingAgentProvider`
contract in `agent.cli_backends`. Workers execute the resulting command or
cloud session, but never select providers, approve policy transitions, or
orchestrate other workers.

## Product classification

| Provider | Integration | Free class | Headless path | Default state |
| --- | --- | --- | --- | --- |
| Qwen Code | CLI | `open_source_byok` | `qwen -p ... --output-format stream-json` | opt-in |
| Gemini CLI | CLI | `free_tier_limited` | `gemini -p ... --output-format stream-json` | opt-in |
| GitHub Copilot CLI | CLI | `free_tier_limited` | `copilot -p ... --no-ask-user` | opt-in |
| Cline | CLI | `open_source_byok` | `cline --json --auto-approve true ...` | opt-in |
| Kilo Code | CLI | `open_source_byok` | `kilo run --auto ...` | opt-in |
| Aider | CLI | `open_source_byok` | `aider --message ... --yes` | existing, opt-in |
| Google Jules | cloud agent | `free_tier_limited` | official `v1alpha` sessions API | opt-in |
| Windsurf | IDE external | `paid_or_unknown` | no verified external headless contract | unsupported |

`open_source_byok` describes the client, not the inference. It does not promise
free model use. In particular, Qwen Code's former OAuth free tier ended on
2026-04-15 according to its current upstream README. Provider quota and account
entitlements are runtime data and are not hard-coded as permanent numbers.

## Automation contract

Every CLI launch uses an absolute executable path and an argument vector. No
shell is involved. The common process adapter provides:

- a hard wall-clock timeout and cancellation signal;
- bounded stdout/stderr collection with streaming events;
- termination of the complete process group;
- an explicit environment allowlist and output secret redaction;
- deterministic reason codes for timeout, cancellation, output overflow, and
  process failure.

There is no human-in-the-loop fallback. A provider state that would require a
question, feedback, or an unapproved plan ends as a bounded blocked/failed
result. Explicit Hub policy may instead select an official automatic approval
mode. Tests use injected process and HTTP ports and never require a person.

Qwen is the reference CLI profile. Its automatic write mode uses `auto-edit`,
structured streaming, session resume, a tool-call limit, and a wall-clock
limit. Fully autonomous `yolo` is available only through the explicit
`coding_agent_permission_mode=autonomous` routing policy and relies on the
existing per-Worker container boundary. Qwen documents that `yolo` itself is
not a sandbox.

Cline receives an explicit command policy denying `sudo` and recursive delete
patterns. Read-only requests use plan mode; write and autonomous requests use
the official auto-approval flag. Jules sessions set `requirePlanApproval=false`
only when the Hub policy authorizes automatic approval. A Jules state requiring
user feedback is returned immediately instead of being kept alive.

## Cost-aware selection

The pure Hub selection policy orders eligible providers as follows:

1. `included_free_inference`
2. `free_tier_limited`
3. `open_source_byok`
4. `paid_or_unknown`

Unavailable providers and exhausted quotas are skipped. The last category is
never selected unless `allow_paid_or_unknown` is explicitly enabled. Capability
requirements are checked before selection, so a cheaper provider cannot be
substituted if it lacks a required tool, structured-output, or sandbox feature.

## Discovery and API projection

`GET /sgpt/capability-matrix` exposes `integration_kind`, `free_class`, the
closed capability map, and the automation classification. CLI binary discovery
uses `shutil.which`; version probes are bounded. Ananta deliberately does not
read provider-owned credential files. Auth therefore remains `unknown` when no
supported environment credential is visible, and the provider CLI owns any
cached login state.

## Upstream contracts

- [Qwen Code headless mode](https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/headless.md)
- [Qwen Code README and authentication status](https://github.com/QwenLM/qwen-code/blob/main/README.md)
- [Gemini CLI headless mode](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md)
- [Gemini CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)
- [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
- [Cline CLI overview](https://docs.cline.bot/usage/cli-overview)
- [Kilo Code CLI](https://kilo.ai/docs/code-with-ai/platforms/cli)
- [Google Jules API](https://developers.google.com/jules/api)
- [Aider scripting](https://aider.chat/docs/scripting.html)
- [Windsurf terminal documentation](https://docs.windsurf.com/windsurf/terminal)

