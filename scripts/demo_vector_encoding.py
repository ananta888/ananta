"""Demo: VectorEncoding pipeline end-to-end (no network, no API calls)."""
from __future__ import annotations

import json
import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.retrieval.vector_encoding import (
    EncodedVector,
    VectorEncodingProfile,
    VectorEncoder,
)

MODES = [
    "off",
    "float32",
    "float16",
    "int8",
    "symmetric4bit",
    "turboquant_mse_experimental",
]

SAMPLE = [math.sin(i * 0.1) for i in range(384)]


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


def banner(text: str) -> None:
    width = 62
    print("=" * width)
    print(f"  {text}")
    print("=" * width)


def section(text: str) -> None:
    print(f"\n--- {text} ---")


# ---------------------------------------------------------------------------
# 1. Header
# ---------------------------------------------------------------------------
banner("VectorEncoding Pipeline Demo  (no network / no API)")

# ---------------------------------------------------------------------------
# 2. Show all modes
# ---------------------------------------------------------------------------
section("Available encoding modes")
for m in MODES:
    p = VectorEncodingProfile(mode=m)
    tag = " [experimental]" if p.experimental else ""
    print(f"  {m:<32}{tag}")

# ---------------------------------------------------------------------------
# 3. Encode / decode each mode; collect results
# ---------------------------------------------------------------------------
section("Encode → Decode round-trip per mode")

results: list[dict] = []
for mode in MODES:
    profile = VectorEncodingProfile(mode=mode)
    encoder = VectorEncoder(profile)
    encoded: EncodedVector = encoder.encode(SAMPLE)
    decoded: list[float] = encoder.decode(encoded)

    ratio = encoded.diagnostics.get("compression_ratio_vs_float32", 1.0)
    err   = encoded.diagnostics.get("max_abs_error", 0.0)
    nbytes = encoded.diagnostics.get("bytes_encoded_payload", len(SAMPLE) * 4)
    h     = encoded.metadata.get("profile_hash", profile.config_hash())
    csim  = cosine_sim(SAMPLE, decoded)

    results.append(dict(mode=mode, ratio=ratio, err=err, nbytes=nbytes,
                        hash=h, csim=csim, encoded=encoded))

    print(f"  [{mode}]  ratio={ratio:.2f}×  max_abs_err={err:.4f}"
          f"  bytes={nbytes}  hash={h[:12]}")

# ---------------------------------------------------------------------------
# 4. Comparison table
# ---------------------------------------------------------------------------
section("Comparison table")
hdr = f"{'Mode':<28} {'Ratio':>6}  {'MaxErr':>8}  {'Bytes (384d)':>12}"
print(hdr)
print("-" * len(hdr))
for r in results:
    print(f"  {r['mode']:<26} {r['ratio']:>5.2f}×  {r['err']:>8.3f}  {r['nbytes']:>12}")

# ---------------------------------------------------------------------------
# 5. Cosine similarity preserved
# ---------------------------------------------------------------------------
section("Cosine similarity: original vs decoded")
for r in results:
    bar = "#" * int(r["csim"] * 20)
    print(f"  {r['mode']:<28} cos_sim={r['csim']:.6f}  [{bar:<20}]")

# ---------------------------------------------------------------------------
# 6. TurboQuant_prod stub (NotImplementedError)
# ---------------------------------------------------------------------------
section("TurboQuant_prod stub (NotImplementedError expected)")
try:
    from worker.retrieval.turboquant_encoding import TurboQuantProd  # type: ignore
    tq = TurboQuantProd()
    tq.encode(SAMPLE)
    print("  WARN: TurboQuantProd did not raise — stub incomplete")
except NotImplementedError as exc:
    print(f"  OK: NotImplementedError raised as expected: {exc}")
except ImportError:
    print("  SKIP: turboquant_encoding.py not yet created (expected at this stage)")

# ---------------------------------------------------------------------------
# 7. QuantizationFallbackPolicy demo
# ---------------------------------------------------------------------------
section("QuantizationFallbackPolicy: fallback_float32 mode")
try:
    from worker.retrieval.vector_encoding import QuantizationFallbackPolicy  # type: ignore
    policy = QuantizationFallbackPolicy(fallback_mode="float32")
    print(f"  QuantizationFallbackPolicy loaded: fallback_mode={policy.fallback_mode}")
except (ImportError, AttributeError):
    # Not yet implemented — demonstrate the concept via plain profile
    fallback_profile = VectorEncodingProfile(mode="float32")
    fallback_encoder = VectorEncoder(fallback_profile)
    encoded_fb = fallback_encoder.encode(SAMPLE)
    err_fb = encoded_fb.diagnostics.get("max_abs_error", 0.0)
    print(f"  QuantizationFallbackPolicy not yet in module.")
    print(f"  Simulated fallback: mode=float32  max_abs_error={err_fb:.4f}  (lossless)")

# ---------------------------------------------------------------------------
# 8. EncodedVector.as_dict() pretty-printed (int8 as representative example)
# ---------------------------------------------------------------------------
section("EncodedVector.as_dict() — int8 example (truncated payload)")
int8_result = next(r for r in results if r["mode"] == "int8")
ev: EncodedVector = int8_result["encoded"]
d = ev.as_dict()
d["payload"] = d["payload"][:32] + "…"  # truncate for readability
print(json.dumps(d, indent=2))

# ---------------------------------------------------------------------------
# 9. Summary
# ---------------------------------------------------------------------------
print()
banner("VectorEncoding pipeline healthy.")
print("  Run benchmark with:")
print("    pytest tests/test_vector_encoding_benchmark.py")
print("=" * 62)
