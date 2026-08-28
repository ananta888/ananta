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

Worker training is connected through a narrow knowledge-expert lifecycle port.
The production PEFT/TRL adapter checks the delegated task, attempt, backend,
base-model, tokenizer, dataset and LoRA bindings before training. It discovers
the model's actual transformer layout and limits LoRA targets to the complete
final FFN block. Exactly one confined, non-symlink safetensors adapter is
admitted. The generic PEFT backend exposes only a configuration factory hook,
so the expert constraint extends the backend without moving policy into it.

Inference uses an explicit capability record. Dynamic composition requires
all of: final-FFN compatibility, entropy access, atomic switching and declared
KV-cache safety. Missing capability produces a visible RAG fallback. Experts
with overlapping Knowledge Units are not composed; a promotion gate must
provide pairwise and multi-expert results before admission.

Rollout state is persistent and Hub-owned. A closed admission record starts
shadow evaluation; bounded observations advance automatically to a
deterministic canary and then GA. Activation and rollback use the registry's
compare-and-swap generation port. Workers consume decisions but neither own
the release state nor promote another generation. If the registry or a real,
attested dynamic-runtime capability is absent, composition remains unavailable
and requests retain the RAG/base fallback.

This split protects SRP and DIP: policy and orchestration stay in Hub services,
execution stays behind Worker ports, and contracts are runtime-neutral.
