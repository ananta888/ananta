from scripts.run_sfu_broadcast_contract_parity import evaluate_runtime_reports


def test_parity_reports_require_both_complete_runtimes() -> None:
    status, reasons, counts = evaluate_runtime_reports(
        {"tests": 12, "failures": 0, "errors": 0, "skipped": 0},
        {
            "success": True,
            "numTotalTests": 12,
            "numPassedTests": 12,
            "numFailedTests": 0,
            "numPendingTests": 0,
        },
    )
    assert status == "passed"
    assert reasons == ()
    assert counts == {"python_tests": 12, "typescript_tests": 12}


def test_parity_reports_reject_skipped_or_pending_cases() -> None:
    status, reasons, _counts = evaluate_runtime_reports(
        {"tests": 12, "failures": 0, "errors": 0, "skipped": 1},
        {
            "success": True,
            "numTotalTests": 12,
            "numPassedTests": 11,
            "numFailedTests": 0,
            "numPendingTests": 1,
        },
    )
    assert status == "failed"
    assert reasons == (
        "contract_parity_python_failed_or_incomplete",
        "contract_parity_typescript_failed_or_incomplete",
    )
