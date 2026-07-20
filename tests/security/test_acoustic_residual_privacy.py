from __future__ import annotations

import math
import struct

import pytest

from voice_runtime.features.residual import AcousticResidualAdapter


def test_residual_requires_grant_before_sample_access_and_purges_on_revoke() -> None:
    adapter = AcousticResidualAdapter()
    with pytest.raises(PermissionError, match="grant_required"):
        adapter.extract(grant_active=False, pcm_s16le=b"not-even-valid-pcm", sample_rate_hz=16_000)
    values = adapter.extract(grant_active=True, pcm_s16le=struct.pack("<h", 1000) * 2048, sample_rate_hz=16_000)
    assert len(values) <= 128
    decision = adapter.assess_privacy(values)
    assert not decision.allowed
    assert decision.reason_code == "residual_privacy_calibration_no_go"
    with pytest.raises(PermissionError):
        adapter.extract(grant_active=False, pcm_s16le=b"", sample_rate_hz=16_000)


def test_manipulated_or_high_risk_residual_is_no_go() -> None:
    adapter = AcousticResidualAdapter()
    invalid = adapter.assess_privacy((math.nan,))
    assert not invalid.allowed and invalid.reason_code == "residual_invalid"
    high_detail = tuple(index / 127 for index in range(128))
    assert not adapter.assess_privacy(high_detail).allowed
