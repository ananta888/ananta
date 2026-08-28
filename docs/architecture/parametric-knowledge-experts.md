# Parametric knowledge experts

Parametric experts extend, but do not replace, CodeCompass retrieval. The Hub
remains the control plane: it evaluates knowledge eligibility, owns tasks,
admits signed immutable manifests and banks, selects rollout policy and changes
the active generation with compare-and-swap. A Worker compiles or trains one
assigned task and may load only the exact admitted generation. It cannot
publish, activate, delegate or schedule another task.

The contracts bind every expert to tenant, workspace, repository, Knowledge
Units, source revisions, base-model and tokenizer digests, architecture,
final-FFN target modules, runtime version, evaluation and policy decisions.
Only safetensors artifacts with matching size and SHA-256 are loadable.

Inference uses an explicit capability record. Dynamic composition requires
all of: final-FFN compatibility, entropy access, atomic switching and declared
KV-cache safety. Missing capability produces a visible RAG fallback. Experts
with overlapping Knowledge Units are not composed; a promotion gate must
provide pairwise and multi-expert results before admission.

This split protects SRP and DIP: policy and orchestration stay in Hub services,
execution stays behind Worker ports, and contracts are runtime-neutral.
