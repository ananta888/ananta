from agent.services.regression_gate_service import RegressionGateService


def test_regression_gate_rejects_wrong_output(tmp_path):
    result = RegressionGateService().evaluate(
        workspace_dir=tmp_path,
        expected_output="ok",
        actual_output="bad",
    )
    assert result["status"] == "rejected"


def test_regression_gate_blocks_unsafe_command(tmp_path):
    result = RegressionGateService().evaluate(workspace_dir=tmp_path, test_commands=["rm -rf /"])
    assert result["status"] == "rejected"
    assert result["test_results"][0]["reason_code"] == "policy_denied"
