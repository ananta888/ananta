# Qwen3.8-27B abliterated hardware evaluation

The target is an RTX 3080 10 GiB plus a 64 GB host. Every selected artifact
requires CPU/GPU offload. The first paired candidates are UD-IQ3_XXS
(11,021,122,464 bytes) and UD-IQ4_XS (14,403,496,864 bytes). Q2_K is a quality
boundary, not a VRAM-fit recommendation.

`benchmarks/local/qwen38_abliterated_rtx3080.yaml` fixes 4K/8K/16K/32K
contexts, one request, no swap growth and 15 percent reserve. All measurements
start `not_run`; file size is not a runtime result.
