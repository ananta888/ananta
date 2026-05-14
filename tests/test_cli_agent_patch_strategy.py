from worker.core.cli_agent_patch_strategy import CliAgentPatchStrategy
from worker.core.propose_orchestrator import ProposeContext
from worker.core.propose import STATUS_DECLINED, STATUS_ADVISORY


def test_cli_patch_declines_without_diff():
    ctx = ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="hello")
    result = CliAgentPatchStrategy().run(ctx)
    assert result.status == STATUS_DECLINED


def test_cli_patch_returns_advisory_for_diff():
    diff = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    
    ctx = ProposeContext(goal_id="g", task_id="t", task={}, base_prompt="x", research_context={"raw_output": diff})
    result = CliAgentPatchStrategy().run(ctx)
    assert result.status == STATUS_ADVISORY
    assert result.reason == "patch_proposal_extracted"
