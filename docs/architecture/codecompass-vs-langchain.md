# CodeCompass vs LangChain — Source-of-Truth Rules

A short reference to settle recurring questions about which subsystem
"owns" a piece of context, an index, or a query path. Both systems
are present in Ananta; the rules below describe which one to use for
which job.

## TL;DR

CodeCompass is the **canonical context, index, and source-of-truth
layer** for Ananta. LangChain is an **optional chain/rag/tool
executor** that *consumes* CodeCompass. They are complementary, not
competitors.

## Responsibility matrix

| Concern                                   | CodeCompass | LangChain/LangGraph | Hub |
|-------------------------------------------|:-----------:|:-------------------:|:---:|
| Canonical file/symbol index               | ✔︎           | ✘                   |     |
| Persistent project embedding store        | ✔︎           | ✘ (cache only)      |     |
| Free-text + graph + vector hybrid query   | ✔︎           | ✘                   |     |
| Source citations in answers               | ✔︎           | (re-emits)          |     |
| Chain composition (prompts → LLM → tools) |             | ✔︎                   |     |
| Stateful agent graphs / human-in-loop     |             | ✔︎ (LangGraph)       |     |
| External LLM API calls                    |             | ✔︎                   | (gated) |
| Per-tool allowlist / network policy       |             | ✔︎                   | ✔︎ |
| Task plan / DAG                           |             |                     | ✔︎ |
| Approval gate                             |             | (asks)              | ✔︎ |
| Audit log                                 |             | (writes trace)      | ✔︎ |
| Artifact final storage                    |             | (writes)            | ✔︎ |
| Verification / regression                 |             |                     | ✔︎ |

## When to use which

### Use CodeCompass when you need:

- A canonical answer to *"what symbols / files / lines relate to
  this concept?"* — the resolver returns ranked candidates with
  grounded paths.
- A free-text or graph query against the live project index.
- A retrieval that must produce **citable** source-pack IDs (so the
  Hub can verify the answer against the original sources later).

### Use LangChain/LangGraph when you need:

- A multi-step chain that combines prompt templates, an LLM, and
  tools (web search, code analysis, summarization).
- A stateful agent that loops over an LLM with persistent checkpoints.
- An existing LangChain component the team already maintains
  (e.g. a custom Retriever wrapping CodeCompass results).

### Do not use LangChain to:

- Build a parallel project index. CodeCompass is the only index
  LangChain may consult. If LangChain needs more context, it must ask
  CodeCompass.
- Replace the Hub's task plan. LangGraph's checkpoint store is
  ephemeral or Hub-owned; it never overwrites Hub task state.
- Bypass the policy gate. Every tool call goes through
  `WorkflowPolicyGate`.
- Auto-merge, push, or apply patches without Hub approval.

## Source-grounded answers (the hard rule)

The Hub's source-grounded answer rule applies to both systems:
*agents and workers must never invent source identifiers.* When a
LangChain chain answers a question, the citations it returns must
trace back to CodeCompass source-pack IDs (or to a verified artifact
the Hub has registered). A chain that invents file paths or line
numbers is treated as a failed run, not a partial answer.

## Local-first, no surprise network calls

Default `model_provider_ref` is `local.default`. The
`external_calls_allowed` flag is `False` by default. Cloud providers
require `mode=cloud_gated` plus `external_calls_allowed=True` and
are blocked by `LangChainProviderConfig` validation otherwise. The
provider diagnostic reports `local`/`cloud`/`degraded` so the Hub
can show the user which model answered.

## Why not LangSmith?

LangSmith is LangChain's hosted tracing service. Ananta does not
require it. The Hub has its own audit service
(`agent.services.audit_service`) and its own trace format
(`WorkflowArtifactResult.execution_trace`). Local file/console
output is enough for the default. Adding LangSmith is a separate
integration and only happens if the user explicitly enables it in
their profile — never by default.
