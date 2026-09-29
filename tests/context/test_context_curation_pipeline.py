from agent.services.context_curation_pipeline import (
    ContextCurationPipeline, CurationPolicy, RawCandidate, DropReason
)
import pytest

def _make(path="src/foo.py", score=0.8, snippet="x" * 100, provider="codecompass", active_status="active"):
    return RawCandidate(provider=provider, path=path, snippet=snippet, score=score, active_status=active_status)

def test_denied_path_dropped():
    p = CurationPolicy(allowed_paths=[], denied_paths=[])
    pipe = ContextCurationPipeline(p)
    c = _make(path=".env")
    trace = pipe.curate([c], "q")
    assert any(d.reason == DropReason.DENIED_PATH for d in trace.dropped)

def test_git_path_always_denied():
    pipe = ContextCurationPipeline()
    c = _make(path=".git/config")
    trace = pipe.curate([c], "q")
    assert any(d.reason == DropReason.DENIED_PATH for d in trace.dropped)

def test_duplicate_same_path_keeps_highest_score():
    pipe = ContextCurationPipeline()
    c1 = _make(path="a.py", score=0.9)
    c2 = _make(path="a.py", score=0.5)
    trace = pipe.curate([c1, c2], "q")
    assert trace.output_count == 1
    assert trace.selected[0].score == 0.9

def test_rank_by_freshness_affects_order():
    pipe = ContextCurationPipeline()
    fresh = RawCandidate("cc", "fresh.py", "x" * 100, 0.7, freshness=1.0)
    stale = RawCandidate("cc", "stale.py", "x" * 100, 0.75, freshness=0.1)
    trace = pipe.curate([stale, fresh], "q")
    assert trace.output_count == 2

def test_active_status_dead_candidate_penalty():
    policy = CurationPolicy(allowed_paths=[], denied_paths=[], min_score=0.1)
    pipe = ContextCurationPipeline(policy)
    dead = RawCandidate("cc", "dead.py", "x" * 100, 0.8, active_status="dead_candidate")
    active = RawCandidate("cc", "live.py", "x" * 100, 0.5, active_status="active")
    trace = pipe.curate([dead, active], "q")
    assert trace.output_count == 2
    # active should rank higher despite lower raw score
    assert trace.selected[0].path == "live.py"

def test_snippet_truncated():
    policy = CurationPolicy(allowed_paths=[], denied_paths=[], max_snippet_chars=50)
    pipe = ContextCurationPipeline(policy)
    c = _make(snippet="a" * 200)
    trace = pipe.curate([c], "q")
    assert trace.selected[0].truncated is True

def test_budget_fit_removes_over_budget():
    policy = CurationPolicy(allowed_paths=[], denied_paths=[], budget_chars=100, max_items=10)
    pipe = ContextCurationPipeline(policy)
    # 3 candidates each 60 chars → only 1 fits in budget
    candidates = [_make(path=f"f{i}.py", snippet="x" * 60, score=0.9 - i * 0.1) for i in range(3)]
    trace = pipe.curate(candidates, "q")
    assert any(d.reason == DropReason.OVER_BUDGET for d in trace.dropped)

def test_trace_has_all_dropped():
    pipe = ContextCurationPipeline()
    c1 = _make(path=".env")  # will be denied
    c2 = _make(path="good.py")
    trace = pipe.curate([c1, c2], "q")
    assert len(trace.dropped) >= 1

def test_empty_candidates():
    trace = ContextCurationPipeline().curate([], "q")
    assert trace.output_count == 0
    assert trace.dropped == []

def test_item_id_deterministic():
    pipe = ContextCurationPipeline()
    c1 = _make()
    c2 = _make()
    t1 = pipe.curate([c1], "q")
    t2 = pipe.curate([c2], "q")
    assert t1.selected[0].item_id == t2.selected[0].item_id

def test_min_score_filter():
    policy = CurationPolicy(allowed_paths=[], denied_paths=[], min_score=0.5)
    pipe = ContextCurationPipeline(policy)
    c = _make(score=0.3)
    trace = pipe.curate([c], "q")
    assert any(d.reason == DropReason.LOW_SCORE for d in trace.dropped)
