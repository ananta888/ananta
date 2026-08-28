# DMoE parametric knowledge: reproduction gate

Reviewed on 2026-08-27: [arXiv:2606.14243](https://arxiv.org/abs/2606.14243)
and its [HTML paper](https://arxiv.org/html/2606.14243). The reviewed version
is v1, submitted 2026-06-12 under CC BY 4.0. Exact title/author searches did
not identify an official implementation or checkpoint; that absence is not
evidence that no later artifact can exist.

The paper reports Llama-3.2-1B-Instruct and Qwen2.5-1.5B-Instruct, 27,613 DPR
Wikipedia passages, Elasticsearch BM25 with top-k 3, LoRA rank 4 and alpha
16, learning rate 1e-5, one epoch, final-layer FFN experts, an entropy
threshold of 2.0, greedy decoding and A100 80GB measurements. Results are not
uniformly best across every evaluated setting, so this repository does not
treat the paper as a production-capability claim.

## Gate

Status is **NO-GO / blocked** until all of the following exist:

1. a source- and digest-bound local reproduction against the same dense
   baseline;
2. measured quality, VRAM, RAM, disk, training cost/time and switch latency;
3. a framework capability probe proving final-FFN placement, entropy access,
   atomic composition and KV-cache safety;
4. security and general holdouts with no accepted regression;
5. a Hub-controlled canary and atomic rollback drill.

Ports, contracts and test doubles in Ananta are scaffolding, not reproduction
evidence. Generic PEFT loading must never satisfy this gate by inference or
runtime-name heuristics.
