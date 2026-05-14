from worker.core.hermes_proposal_strategy import HermesProposalStrategy
from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import STATUS_DECLINED, STATUS_ADVISORY


def test_hermes_declines_without_prompt():
    ctx = ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="")
    result = HermesProposalStrategy().run(ctx)
    assert result.status == STATUS_DECLINED


def test_hermes_returns_advisory_not_executable():
    ctx = ProposeContext(goal_id="g", task_id="t", task={"description": "Plan migration"}, base_prompt="x")
    result = HermesProposalStrategy().run(ctx)
    assert result.status == STATUS_ADVISORY
    assert result.reason == "hermes_proposal_generated"
