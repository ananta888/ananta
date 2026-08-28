# DMoE reproduction workspace

This directory is reserved for a source-bound reproduction of the paper
“Decoupled Mixture-of-Experts for Parametric Knowledge Injection”. Product rollout remains blocked until a measured run is
available.

The first experiment must pin the exact model, tokenizer, dataset, software
and hardware digests. It must compare the same held-out examples across
`dense`, `rag`, `sft_lora`, `single_expert`, `multi_expert`, and
`expert_plus_rag`. Raw prompts, secrets, private source text and runtime
credentials must not be committed.

Required measurements are quality, general and security holdouts, training
time/cost, VRAM, RAM, disk, retrieval latency, cold/warm load latency,
hot-switch latency and inference latency. Results belong in a structured,
deterministic audit artifact only after they have actually been measured.

Current decision: **NO-GO**. The paper describes its experimental setup but
does not provide an official repository or checkpoint link in the reviewed
version, and Ananta has no local reproduction or framework capability proof.
