from agent.services.benchmark_suite import (
    BenchmarkSuiteRunner, WorkflowKind, ProviderMode, EvalVerdict,
    GoldExpectation, WorkflowSpec, make_workflow
)
import pytest

def _runner(fake_retrieval=None):
    return BenchmarkSuiteRunner(fake_retrieval=fake_retrieval or {})

def test_run_all_empty():
    runner = _runner()
    run = runner.run_all(name="empty")
    assert run.total_workflows == 0
    assert run.pass_rate() == 0.0

def test_run_single_workflow_fake():
    runner = _runner(fake_retrieval={"find auth": ["auth.py", "user.py"]})
    spec = make_workflow(name="auth", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="find auth",
                        expected_files=["auth.py", "user.py"])
    runner.register_workflow(spec)
    run = runner.run_all(name="test")
    assert run.total_workflows == 1
    assert run.passed == 1
    assert run.results[0].eval_result.verdict == EvalVerdict.PASS

def test_miss_gives_partial_or_fail():
    runner = _runner(fake_retrieval={"query": ["a.py"]})
    spec = make_workflow(name="w", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="query",
                        expected_files=["a.py", "b.py", "c.py"])
    runner.register_workflow(spec)
    run = runner.run_all()
    assert run.results[0].eval_result.verdict in (EvalVerdict.PARTIAL, EvalVerdict.FAIL)
    assert "b.py" in run.results[0].eval_result.files_missed or "c.py" in run.results[0].eval_result.files_missed

def test_no_gold_set_gives_no_gold_set_verdict():
    runner = _runner()
    spec = make_workflow(name="no_gold", kind=WorkflowKind.PR_REVIEW, query="review")
    runner.register_workflow(spec)
    run = runner.run_all()
    assert run.results[0].eval_result.verdict == EvalVerdict.NO_GOLD_SET

def test_pass_rate_calculation():
    runner = _runner(fake_retrieval={"q1": ["a.py"], "q2": []})
    spec1 = make_workflow(name="w1", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q1", expected_files=["a.py"])
    spec2 = make_workflow(name="w2", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q2", expected_files=["b.py"])
    runner.register_workflow(spec1)
    runner.register_workflow(spec2)
    run = runner.run_all()
    assert run.total_workflows == 2
    assert run.passed >= 1

def test_as_dict_contains_results():
    runner = _runner()
    spec = make_workflow(name="w", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q")
    runner.register_workflow(spec)
    run = runner.run_all()
    d = run.as_dict()
    assert "results" in d
    assert "pass_rate" in d
    assert "total_latency_ms" in d

def test_to_markdown_has_table():
    runner = _runner(fake_retrieval={"q": ["a.py"]})
    spec = make_workflow(name="test wf", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q")
    runner.register_workflow(spec)
    run = runner.run_all(name="My Benchmark")
    md = run.to_markdown()
    assert "My Benchmark" in md
    assert "test wf" in md
    assert "|" in md  # table

def test_approval_gates_tracked():
    runner = _runner()
    spec = make_workflow(name="w", kind=WorkflowKind.CODE_GENERATION, query="q", requires_approval=True)
    runner.register_workflow(spec)
    run = runner.run_all()
    assert run.results[0].sample.approval_gates_triggered == 1

def test_cost_tracked():
    runner = _runner(fake_retrieval={"q": ["a.py"]})
    spec = make_workflow(name="w", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q", provider_mode=ProviderMode.LOCAL_ONLY)
    runner.register_workflow(spec)
    run = runner.run_all()
    assert isinstance(run.total_cost_units, float)

def test_run_single_workflow_by_id():
    runner = _runner(fake_retrieval={"q": ["x.py"]})
    spec = make_workflow(name="single", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q", expected_files=["x.py"])
    runner.register_workflow(spec)
    result = runner.run_workflow(spec.workflow_id)
    assert result is not None
    assert result.eval_result.verdict == EvalVerdict.PASS

def test_run_unknown_workflow_id():
    runner = _runner()
    result = runner.run_workflow("nonexistent")
    assert result is None

def test_fake_mode_has_zero_cost():
    runner = _runner(fake_retrieval={"q": ["a.py"]})
    spec = make_workflow(name="w", kind=WorkflowKind.CONTEXT_RETRIEVAL, query="q", provider_mode=ProviderMode.FAKE)
    runner.register_workflow(spec)
    run = runner.run_all()
    assert run.results[0].sample.cost_units == 0.0
