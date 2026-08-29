# ADR: DSPy is an optional optimization engine

- Status: accepted
- Date: 2026-08-29

DSPy is integrated only behind the execution-only optimization worker. It is
not a provider, workflow runtime, task queue, retriever authority or promotion
authority. The Hub creates and fences jobs, resolves every provider binding,
sets budgets, evaluates candidates and applies or rolls back registry changes.
A worker executes one delegated run and cannot create tasks or call workers.

Phase 1 allows Predict, ChainOfThought and a bounded RAG composite plus
LabeledFewShot and BootstrapFewShot. ReAct, code execution, MCP/tool imports,
dynamic modules, weight optimization and fine-tuning are excluded. Ananta's
CodeCompass port remains the only production retrieval authority.

The persisted interchange is `ananta.prompt-program.v1`, canonical JSON with
closed fields and no Python objects. Pickle, cloudpickle load, DSPy full-program
load and arbitrary paths are forbidden. This provides a DSPy-free production
renderer and exit path; DSPy stays out of the normal inference hot path.

Promotion may run fully automatically only after every deterministic,
security, evaluation, cost, evidence and revision gate has passed. Missing
human interaction is never a waiting state. A failed gate remains blocked and
keeps the baseline active.

The design protects SRP by separating contracts, policy, state, job lifecycle,
engine adapter, LM/retrieval bridges, serialization, evaluation, artifact store
and promotion. DIP/ISP allow network-free fakes. OCP confines upstream changes
to the optional adapter. LSP is enforced by shared port contract tests.
