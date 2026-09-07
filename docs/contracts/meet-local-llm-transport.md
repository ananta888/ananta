# Local Meet LLM transport boundary

## Source check before implementation (MAP-27)

`worker/meet_media/llm.py` already sends room text only as a user message under
a fixed system prompt, supplies no tools/history, disables environment proxies,
and requires the configured model digest with positive GPU residency. However,
its default HTTP opener follows redirects; the model-list response is truncated
without an overflow check, and network/configuration concerns are mixed into
answer generation. An HTTP redirect is not a Hub grant to contact another
provider. This slice does not claim that prompt wording prevents injection.

## Bounded change

Extract a small operator-configured Ollama JSON transport with only `chat` and
`models` operations. Reject malformed URLs, embedded credentials, unexpected
paths, query strings and fragments before opening a socket. Keep existing Docker
and loopback URLs compatible; configuration remains operator-owned, not selected
by room contents. Reject every redirect, including same-origin redirects, and
keep environment proxies disabled. Never retry or introduce a cloud fallback.

Bound both response bodies to 64 KiB and use the existing deadline-aware HTTP
reader. Preserve chat/model-list socket budgets (60/5 seconds); a blocked socket
read can still last its socket timeout, so do not claim a hard real-time wall
deadline. Return stable error codes without upstream bodies, prompts or URLs.
Keep fixed prompts, exact response limits, model/GPU checks and the public
`answer`/`generate` behavior. Transport injection gives tests a narrow seam
(SRP/DIP/ISP); no new Worker orchestration, task authority or provider selection.

## Headless verification

Use actual loopback HTTP servers for redirects on chat and model-list requests;
verify the redirect destination is never contacted. Test response overflow,
malformed JSON and configuration, slow-read deadlines, closed request paths,
no inherited proxy and exact prompt/token behavior with deterministic fixtures.
Re-run existing response-budget and media-boundary tests. No Docker, GPU,
external provider, production secret, human approval or fabricated evidence ID
is needed. Actual GPU/browser acceptance remains a separate open gate.
