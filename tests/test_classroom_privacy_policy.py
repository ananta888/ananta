from agent.services.classroom.privacy_policy import prune_expired_segments, redact_pii


def test_redacts_name_email_and_phone():
    text, count = redact_pii("Max Mustermann max@example.com +49 123 456789")
    assert count == 3
    assert "Max Mustermann" not in text
    assert "max@example.com" not in text
    assert "456789" not in text


def test_retention_prunes_raw_segments():
    segments = [{"received_at": 0}, {"received_at": 100}]
    assert prune_expired_segments(segments, cfg={"classroom": {"retention_hours_raw_segments": 1}}, now=3699) == [
        {"received_at": 100}
    ]
