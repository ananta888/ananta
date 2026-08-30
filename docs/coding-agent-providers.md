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

Current non-interactive Qwen authentication accepts pre-provisioned provider
credentials such as `BAILIAN_CODING_PLAN_API_KEY`, `DASHSCOPE_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or
`GEMINI_API_KEY`. Endpoint/model selectors such as `OPENAI_BASE_URL`,
`OPENAI_MODEL`, and `QWEN_MODEL` are passed separately and never count as proof
that authentication is ready. The optional live gate is fully automatic:

```bash
ANANTA_QWEN_LIVE_SMOKE=1 \
BAILIAN_CODING_PLAN_API_KEY='<provided-by-ci-secret-store>' \
.venv/bin/python scripts/run_qwen_code_live_smoke.py
```

Without the explicit machine opt-in the gate emits a machine-readable skipped
result. With opt-in it fails closed when binary or auth is absent; it never
starts a login prompt and never prints task output or credentials.

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

## Client and inference target

The coding client and the model endpoint are independent routing dimensions.
OpenCode and Aider can target any local OpenAI-compatible provider that is
declared in `local_openai_backends` (or the built-in Ollama/LM Studio targets):

```json
{
  "local_openai_backends": [
    {"id": "local_coder", "base_url": "http://127.0.0.1:9000/v1", "models": ["qwen3-coder"]}
  ],
  "opencode_runtime": {"target_provider": "local_coder", "target_model": "qwen3-coder"},
  "aider_cli": {"target_provider": "local_coder", "model": "qwen3-coder"}
}
```

The Hub validates the target against declared providers. Runtime metadata and
the Assistant UI report the CLI client, client cost class, inference provider,
and model separately. Unknown CLI-account targets remain `unknown`; Ananta does
not infer or invent them.

## Discovery and API projection

`GET /sgpt/capability-matrix` exposes `integration_kind`, `free_class`, the
closed capability map, and the automation classification. CLI binary discovery
uses `shutil.which`; version probes are bounded. Ananta deliberately does not
read provider-owned credential files. Auth therefore remains `unknown` when no
supported environment credential is visible, and the provider CLI owns any
cached login state.

## Upstream contracts

- [Qwen Code headless mode](https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/headless.md)
- [Qwen Code authentication](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/auth.md)
- [Qwen Code README and authentication status](https://github.com/QwenLM/qwen-code/blob/main/README.md)
- [Gemini CLI headless mode](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md)
- [Gemini CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)
- [GitHub Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
- [Cline CLI overview](https://docs.cline.bot/usage/cli-overview)
- [Kilo Code CLI](https://kilo.ai/docs/code-with-ai/platforms/cli)
- [Google Jules API](https://developers.google.com/jules/api)
- [Aider scripting](https://aider.chat/docs/scripting.html)
- [Windsurf terminal documentation](https://docs.windsurf.com/windsurf/terminal)
