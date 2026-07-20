from __future__ import annotations

from scripts.run_webrtc_crypto_gate import DEFAULT_VECTORS, run


def test_shared_crypto_vector_gate_is_green_and_content_free() -> None:
    report = run(DEFAULT_VECTORS)
    assert report["passed"] is True
    assert len(report["vectors"]) >= 12
    for item in report["vectors"]:
        assert set(item) == {"name", "code", "hash", "passed"}
        assert len(item["hash"]) == 64
