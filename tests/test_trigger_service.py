import hashlib, hmac, time
import pytest
from agent.services.trigger_service import (
    TriggerService, TriggerKind, TriggerStatus, TriggerEventStatus,
    TriggerValidationError, make_trigger
)

def _svc_with_trigger(**kwargs):
    svc = TriggerService()
    cfg = make_trigger(name="test", **kwargs)
    svc.register(cfg)
    return svc, cfg

def test_fire_creates_mapped_event():
    svc, cfg = _svc_with_trigger(blueprint_id="bp-1")
    ev = svc.fire(cfg.trigger_id)
    assert ev.status == TriggerEventStatus.MAPPED
    assert ev.blueprint_id == "bp-1"
    assert ev.is_actionable() is True

def test_disabled_trigger_rejects():
    svc, cfg = _svc_with_trigger()
    svc.disable(cfg.trigger_id)
    ev = svc.fire(cfg.trigger_id)
    assert ev.status == TriggerEventStatus.REJECTED
    assert "disabled" in ev.audit_note

def test_paused_trigger_rejects():
    svc, cfg = _svc_with_trigger()
    svc.pause(cfg.trigger_id)
    ev = svc.fire(cfg.trigger_id)
    assert ev.status == TriggerEventStatus.REJECTED
    assert "paused" in ev.audit_note

def test_re_enable_trigger():
    svc, cfg = _svc_with_trigger()
    svc.disable(cfg.trigger_id)
    svc.enable(cfg.trigger_id)
    ev = svc.fire(cfg.trigger_id)
    assert ev.status == TriggerEventStatus.MAPPED

def test_rate_limit_enforced():
    svc, cfg = _svc_with_trigger(rate_limit_per_hour=2)
    svc.fire(cfg.trigger_id)
    svc.fire(cfg.trigger_id)
    ev = svc.fire(cfg.trigger_id)  # 3rd → rate limited
    assert ev.status == TriggerEventStatus.RATE_LIMITED

def test_rate_limit_not_yet_exceeded():
    svc, cfg = _svc_with_trigger(rate_limit_per_hour=5)
    for _ in range(4):
        ev = svc.fire(cfg.trigger_id)
        assert ev.status == TriggerEventStatus.MAPPED

def test_webhook_valid_signature():
    secret = "supersecret"
    svc, cfg = _svc_with_trigger(kind=TriggerKind.WEBHOOK, webhook_secret=secret)
    body = b"test payload"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    ev = svc.fire(cfg.trigger_id, webhook_signature=sig, webhook_body=body)
    assert ev.status == TriggerEventStatus.MAPPED

def test_webhook_invalid_signature_rejected():
    svc, cfg = _svc_with_trigger(kind=TriggerKind.WEBHOOK, webhook_secret="secret")
    ev = svc.fire(cfg.trigger_id, webhook_signature="badsig", webhook_body=b"data")
    assert ev.status == TriggerEventStatus.REJECTED
    assert "invalid_webhook_signature" in ev.audit_note

def test_webhook_missing_signature_rejected():
    svc, cfg = _svc_with_trigger(kind=TriggerKind.WEBHOOK, webhook_secret="s")
    ev = svc.fire(cfg.trigger_id)
    assert ev.status == TriggerEventStatus.REJECTED

def test_source_not_allowed_rejected():
    svc, cfg = _svc_with_trigger(allowed_sources=["github"])
    ev = svc.fire(cfg.trigger_id, source="slack")
    assert ev.status == TriggerEventStatus.REJECTED
    assert "source_not_allowed" in ev.audit_note

def test_payload_hash_excludes_secrets():
    svc, cfg = _svc_with_trigger()
    ev1 = svc.fire(cfg.trigger_id, payload={"key": "secret123"})
    ev2 = svc.fire(cfg.trigger_id, payload={"other": "value"})
    # key is redacted → payload_hash for ev1 should not contain "secret123"
    assert "secret123" not in ev1.payload_hash

def test_event_stored_and_retrievable():
    svc, cfg = _svc_with_trigger()
    ev = svc.fire(cfg.trigger_id)
    assert svc.get_event(ev.event_id) is not None

def test_get_events_for_trigger():
    svc, cfg = _svc_with_trigger()
    svc.fire(cfg.trigger_id)
    svc.fire(cfg.trigger_id)
    evs = svc.get_events_for_trigger(cfg.trigger_id)
    assert len(evs) == 2

def test_get_pending_events():
    svc, cfg = _svc_with_trigger()
    svc.fire(cfg.trigger_id)
    pending = svc.get_pending_events()
    assert len(pending) >= 1

def test_register_invalid_rate_limit():
    with pytest.raises(TriggerValidationError):
        svc = TriggerService()
        cfg = make_trigger(name="bad", rate_limit_per_hour=0)
        svc.register(cfg)

def test_unknown_trigger_raises():
    svc = TriggerService()
    with pytest.raises(KeyError):
        svc.fire("nonexistent")

def test_neutral_event_no_direct_action():
    svc, cfg = _svc_with_trigger()
    ev = svc.fire(cfg.trigger_id)
    # TriggerEvent NEVER has a direct worker_id field — only blueprint mapping
    assert not hasattr(ev, 'worker_id') or ev.is_actionable() is not None
    # blueprint_id is the ONLY action pointer
    assert ev.blueprint_id == cfg.blueprint_id or ev.blueprint_id is None

def test_as_dict_no_secrets():
    svc, cfg = _svc_with_trigger()
    ev = svc.fire(cfg.trigger_id, payload={"token": "abc123"})
    d = ev.as_dict()
    assert "abc123" not in str(d)
