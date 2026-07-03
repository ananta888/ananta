from agent.services.experts.work_dispatcher_expert import WorkDispatcherExpert
from agent.services.experts.pr_author_expert import PRAuthorExpert
from agent.services.experts.deep_code_reviewer_expert import DeepCodeReviewerExpert, ReviewSeverity
from agent.services.experts.risk_analyst_expert import RiskAnalystExpert
from agent.services.experts.tester_expert import TesterExpert

# WorkDispatcher
def test_dispatcher_creates_plan():
    e = WorkDispatcherExpert()
    plan = e.create_plan(run_id="r1", goal_text="implement user authentication system")
    assert plan.plan_status == "draft"
    assert len(plan.steps) >= 1

def test_dispatcher_unclear_goal_adds_clarification():
    e = WorkDispatcherExpert()
    plan = e.create_plan(run_id="r1", goal_text="do it")
    assert plan.has_open_questions() is True

def test_dispatcher_validate_no_steps():
    from agent.services.experts.work_dispatcher_expert import DispatchPlan
    import time
    e = WorkDispatcherExpert()
    empty = DispatchPlan("p1", "r1", "goal", [], [], [], [], "draft", time.time())
    errs = e.validate_plan(empty)
    assert any("no steps" in err.lower() for err in errs)

def test_dispatcher_plan_starts_draft():
    e = WorkDispatcherExpert()
    plan = e.create_plan(run_id="r1", goal_text="add new API endpoint for users")
    assert plan.plan_status == "draft"

# PRAuthor
def test_pr_author_creates_body():
    e = PRAuthorExpert()
    body = e.create_pr_body(run_id="r1", goal="Fix auth bug", changed_files=["auth.py"])
    assert body.approval_required_for_github is True
    assert "auth" in body.title.lower() or "Fix" in body.title

def test_pr_body_markdown_has_sections():
    e = PRAuthorExpert()
    body = e.create_pr_body(run_id="r1", goal="Fix bug", changed_files=["a.py"])
    md = body.to_markdown()
    assert "Fix bug" in md
    assert "a.py" in md

def test_pr_validate_empty_title():
    e = PRAuthorExpert()
    body = e.create_pr_body(run_id="r1", goal="Fix bug", changed_files=["a.py"])
    body.title = ""
    issues = e.validate_for_github(body)
    assert any("title" in i.lower() for i in issues)

def test_pr_approval_required():
    e = PRAuthorExpert()
    body = e.create_pr_body(run_id="r1", goal="g", changed_files=["a.py"])
    assert body.approval_required_for_github is True

# DeepCodeReviewer
def test_reviewer_blocking_sets_blocked_verdict():
    e = DeepCodeReviewerExpert()
    report = e.create_report(run_id="r1", findings=[{"severity": "blocking", "title": "Critical"}])
    assert report.overall_verdict == "blocked"

def test_reviewer_warning_sets_needs_changes():
    e = DeepCodeReviewerExpert()
    report = e.create_report(run_id="r1", findings=[{"severity": "warning", "title": "Warn"}])
    assert report.overall_verdict == "needs_changes"

def test_reviewer_has_blocking():
    e = DeepCodeReviewerExpert()
    report = e.create_report(run_id="r1", findings=[{"severity": "blocking", "title": "X"}])
    assert report.has_blocking_issues() is True

def test_reviewer_clean_approved():
    e = DeepCodeReviewerExpert()
    report = e.create_report(run_id="r1", findings=[])
    assert report.overall_verdict == "approved"

# RiskAnalyst
def test_risk_github_workflow_elevated():
    e = RiskAnalystExpert()
    r = e.analyze(run_id="r1", changed_files=[".github/workflows/ci.yml"])
    assert ".github/workflows/ci.yml" in r.auto_elevated_by_paths

def test_risk_dockerfile_elevated():
    e = RiskAnalystExpert()
    r = e.analyze(run_id="r1", changed_files=["Dockerfile"])
    assert r.auto_elevated_by_paths

def test_risk_critical_score():
    e = RiskAnalystExpert()
    assert e.verdict_from_score(85) == "critical"

def test_risk_missing_tests_increases_risk():
    e = RiskAnalystExpert()
    r = e.analyze(run_id="r1", changed_files=["src/main.py"], has_tests=False)
    assert r.missing_test_coverage_risk is True

def test_risk_score_total_weighted():
    from agent.services.experts.risk_analyst_expert import RiskDimension
    e = RiskAnalystExpert()
    dims = [RiskDimension("security", 80, "test"), RiskDimension("api_breakage", 20, "test")]
    score = e.score_total(dims)
    assert score > 20  # security weight=2 pulls it up

# Tester
def test_tester_select_returns_selection():
    e = TesterExpert()
    s = e.select_tests(run_id="r1", changed_files=["agent/services/foo.py"])
    assert len(s.proposed_commands) >= 1

def test_tester_create_report_passed():
    e = TesterExpert()
    r = e.create_report(run_id="r1", command="pytest", exit_code=0, duration_ms=1000)
    assert r.status == "passed"

def test_tester_create_report_failed_blocks():
    e = TesterExpert()
    r = e.create_report(run_id="r1", command="pytest", exit_code=1, duration_ms=500, policy_blocks_on_failure=True)
    assert r.status == "failed"
    assert r.blocks_apply is True

def test_tester_report_not_run():
    e = TesterExpert()
    r = e.report_not_run("r1")
    assert r.status == "not_run"

def test_tester_timeout_flag():
    e = TesterExpert()
    r = e.create_report(run_id="r1", command="pytest", exit_code=-1, duration_ms=60000, timeout=True)
    assert r.timeout is True
    assert r.status == "timeout"
