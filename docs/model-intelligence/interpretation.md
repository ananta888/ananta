# Interpreting model-analysis results

## Evidence states

Every report section has an explicit state.

`available` means the analyzer produced a schema-valid artifact for the
admitted snapshot. It does not imply that the result is universally correct or
that the model is safe.

`unsupported` means the selected format, runtime or policy does not expose the
capability. Unsupported is not a test failure and must not be rendered as zero.

`not_run` means the capability exists but was not requested or was outside the
selected profile.

`failed` means execution was attempted but could not produce a valid result.
The section includes a stable reason code and no fabricated replacement value.

## Static analysis

Static tensor analysis reports topology, shapes, dtypes, byte sizes and stable
module/layer mappings from Safetensors metadata. It does not infer behaviour,
quality, intent or safety from parameter names.

Tokenizer analysis reports data-only configuration and template digests. It
does not execute tokenizer-owned code. Missing metadata remains
`not_available`.

Quantisation analysis reports declared format metadata and relevant packed
tensor dtypes. It does not silently dequantise or claim numerical equivalence.

## Dynamic traces

Core dynamic traces are local, opt-in and bounded. They contain aggregate
hidden-state statistics. Raw prompts and activations are not part of the
default artifact.

Activation magnitude, attention or layer differences are observations. They
are not proof of a causal circuit. Causal tracing, activation patching, probes
and sparse autoencoders belong to a separately approved follow-up track.

## LoRA and comparisons

LoRA delta analysis binds immutable base and adapter identities. A base mismatch
is an error, not a warning. The analyzer does not merge either artifact.

Evaluation runs are directly comparable only when their profile digests match.
Performance comparisons additionally require compatible hardware, and profiles
may require identical runtimes. Incompatible runs are labelled `incomparable`
and are never ranked.

## Source grounding

Passing local tests alone does not create source-grounded release evidence.
Only supplied valid `SRC_*` and `RUN_*` identifiers may ground a release claim.
Missing identifiers result in `unverified`; unknown identifiers must fail
verification. Tools and agents must never invent them.
