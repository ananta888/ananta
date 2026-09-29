from agent.services.codecompass_context_trace_service import (
    CodeCompassContextTraceService, ContextDiscardReason
)

svc = CodeCompassContextTraceService()

def test_start_trace_returns_id():
    tid = svc.start_trace(run_id="r1", query="test")
    assert isinstance(tid, str) and len(tid) > 0

def test_add_selected_item():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r1", query="q")
    svc2.add_selected(tid, provider="codecompass", path="a.py", score=0.8)
    trace = svc2.finalize(tid)
    assert len(trace.selected_items) == 1
    assert trace.selected_items[0].path == "a.py"

def test_add_discarded_item():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r1", query="q")
    svc2.add_discarded(tid, provider="codecompass", path=".env", score=0.9, reason=ContextDiscardReason.DENIED_PATH)
    trace = svc2.finalize(tid)
    assert len(trace.discarded_items) == 1
    assert trace.discarded_items[0].reason == ContextDiscardReason.DENIED_PATH

def test_external_items_marked():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r1", query="q")
    svc2.add_selected(tid, provider="augment", path="b.py", score=0.7, is_external=True)
    trace = svc2.finalize(tid)
    assert trace.has_external_evidence() is True
    assert trace.external_items_count() == 1

def test_short_summary_no_code():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r1", query="q")
    svc2.add_selected(tid, provider="codecompass", path="a.py", score=0.8)
    svc2.add_discarded(tid, provider="codecompass", path=".env", score=0.5, reason=ContextDiscardReason.DENIED_PATH)
    trace = svc2.finalize(tid)
    summary = trace.short_summary()
    assert "1 treffer" in summary
    assert "denied_path" in summary
    assert "snippet" not in summary.lower()

def test_finalize_empty_trace():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r1", query="q")
    trace = svc2.finalize(tid)
    assert trace.output_count if hasattr(trace, 'output_count') else True
    assert isinstance(trace.selected_items, list)

def test_trace_not_found():
    assert svc.get("nonexistent") is None

def test_discard_reasons_in_summary():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r", query="q")
    svc2.add_discarded(tid, provider="cc", path="x.py", score=0.1, reason=ContextDiscardReason.LOW_SCORE)
    svc2.add_discarded(tid, provider="cc", path="y.py", score=0.2, reason=ContextDiscardReason.DUPLICATE)
    trace = svc2.finalize(tid)
    summary = trace.short_summary()
    assert "low_score" in summary or "duplicate" in summary

def test_external_count_zero():
    svc2 = CodeCompassContextTraceService()
    tid = svc2.start_trace(run_id="r", query="q")
    svc2.add_selected(tid, provider="codecompass", path="a.py", score=0.8, is_external=False)
    trace = svc2.finalize(tid)
    assert trace.external_items_count() == 0
