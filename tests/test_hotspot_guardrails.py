from scripts.check_hotspot_guardrails import build_hotspot_report


def test_hotspot_guardrail_report_marks_over_budget_targets(tmp_path, monkeypatch):
    target = tmp_path / "large.py"
    target.write_text("x = 1\n" * 3, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = build_hotspot_report((("large.py", 2), ("missing.py", 10)))

    by_path = {entry["path"]: entry for entry in report["entries"]}
    assert by_path["large.py"]["status"] == "over_budget"
    assert by_path["large.py"]["lines"] == 3
    assert by_path["missing.py"]["exists"] is False
    assert len(report["over_budget"]) == 1

