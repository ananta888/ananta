"""OHA-004: Tests für ToolOutputCompactionService."""
import pytest
from agent.services.tool_output_compaction_service import (
    CompactionResult,
    ToolOutputCompactionService,
    _build_from_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _svc(**kwargs) -> ToolOutputCompactionService:
    defaults = dict(
        enabled=True,
        fail_open=True,
        builtin_rules_enabled=True,
        project_rules_path=None,
        max_input_chars_for_compaction=200,
        max_output_chars=1000,
        always_preserve_signals=True,
    )
    defaults.update(kwargs)
    return ToolOutputCompactionService(**defaults)


def _long_output(n_lines: int = 300, prefix: str = "line") -> str:
    return "\n".join(f"{prefix} {i}: some verbose log output here" for i in range(n_lines))


# ---------------------------------------------------------------------------
# Short output — pass-through
# ---------------------------------------------------------------------------

def test_short_output_returned_unchanged():
    svc = _svc(max_input_chars_for_compaction=10000)
    result = svc.compact(tool_name="pytest", output="ok\n1 passed")
    assert result.compaction_ratio == 1.0
    assert result.applied_rule_ids == []
    assert "ok" in result.compacted_text


def test_empty_output_pass_through():
    svc = _svc()
    result = svc.compact(tool_name="shell_execute", output="")
    assert result.compacted_text == ""
    assert result.compaction_ratio == 1.0


def test_none_output_handled():
    svc = _svc()
    result = svc.compact(tool_name="shell_execute", output=None)  # type: ignore[arg-type]
    assert isinstance(result, CompactionResult)


# ---------------------------------------------------------------------------
# Compaction triggers
# ---------------------------------------------------------------------------

def test_long_output_is_compacted():
    svc = _svc()
    long = _long_output(300)
    result = svc.compact(tool_name="shell_execute", output=long)
    assert result.compaction_ratio < 1.0
    assert result.output_chars < result.input_chars


def test_compaction_ratio_field_present():
    svc = _svc()
    result = svc.compact(tool_name="shell_execute", output=_long_output(300))
    assert 0.0 < result.compaction_ratio <= 1.0


# ---------------------------------------------------------------------------
# Security / error signal preservation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("signal_line", [
    "ERROR: something went wrong",
    "FAILED tests/test_foo.py::test_bar",
    "Traceback (most recent call last):",
    "AssertionError: expected True",
    "permission denied: /etc/shadow",
    "security policy blocked the action",
    "credential not found",
    "CRITICAL: disk full",
    "unauthorized access attempt",
    "forbidden: 403",
])
def test_security_signal_always_preserved(signal_line: str):
    svc = _svc()
    # Embed signal in the middle of long filler
    filler = "\n".join(f"verbose log line {i}" for i in range(200))
    output = f"{filler}\n{signal_line}\n{filler}"
    result = svc.compact(tool_name="shell_execute", output=output)
    assert signal_line in result.compacted_text, (
        f"Signal line not preserved: {signal_line!r}"
    )
    assert signal_line in result.preserved_signals


def test_multiple_signals_all_preserved():
    svc = _svc()
    filler = "\n".join(f"boring log line {i}" for i in range(200))
    signals = ["ERROR: db connection failed", "Traceback (most recent call last):"]
    output = "\n".join([filler] + signals + [filler])
    result = svc.compact(tool_name="shell_execute", output=output)
    for s in signals:
        assert s in result.compacted_text


# ---------------------------------------------------------------------------
# Tool-specific rules
# ---------------------------------------------------------------------------

def test_pytest_output_compacted():
    svc = _svc()
    output = _long_output(300, prefix="test_output")
    result = svc.compact(tool_name="pytest", output=output)
    assert result.compaction_ratio < 1.0


def test_npm_output_compacted():
    svc = _svc()
    output = _long_output(300, prefix="npm log")
    result = svc.compact(tool_name="npm", output=output)
    assert result.compaction_ratio < 1.0


def test_git_diff_compacted():
    svc = _svc()
    output = _long_output(300, prefix="diff line")
    result = svc.compact(tool_name="git", output=output)
    assert result.compaction_ratio < 1.0


def test_generic_tool_compacted():
    svc = _svc()
    output = _long_output(300, prefix="generic output")
    result = svc.compact(tool_name="unknown_tool_xyz", output=output)
    assert result.compaction_ratio < 1.0


# ---------------------------------------------------------------------------
# Result fields
# ---------------------------------------------------------------------------

def test_original_ref_is_16_hex_chars():
    svc = _svc()
    result = svc.compact(tool_name="pytest", output="hello world")
    assert len(result.original_ref) == 16
    assert all(c in "0123456789abcdef" for c in result.original_ref)


def test_original_ref_is_stable():
    svc = _svc()
    text = "some output text"
    r1 = svc.compact(tool_name="pytest", output=text)
    r2 = svc.compact(tool_name="pytest", output=text)
    assert r1.original_ref == r2.original_ref


def test_applied_rule_ids_not_empty_on_compaction():
    svc = _svc()
    result = svc.compact(tool_name="shell_execute", output=_long_output(300))
    assert len(result.applied_rule_ids) > 0


def test_input_output_chars_reported():
    svc = _svc()
    result = svc.compact(tool_name="pytest", output=_long_output(300))
    assert result.input_chars > 0
    assert result.output_chars > 0


def test_omitted_summary_present_after_compaction():
    svc = _svc()
    result = svc.compact(tool_name="shell_execute", output=_long_output(300))
    # Either the summary is in the compacted_text or in omitted_summary field
    assert result.omitted_summary or "omitted" in result.compacted_text


# ---------------------------------------------------------------------------
# Error parameter
# ---------------------------------------------------------------------------

def test_error_appended_to_output():
    svc = _svc(max_input_chars_for_compaction=10000)
    result = svc.compact(
        tool_name="shell_execute",
        output="stdout line",
        error="stderr: something failed",
    )
    assert "stderr: something failed" in result.compacted_text


def test_error_not_duplicated_if_already_in_output():
    svc = _svc(max_input_chars_for_compaction=10000)
    msg = "some error"
    result = svc.compact(tool_name="pytest", output=msg, error=msg)
    assert result.compacted_text.count(msg) == 1


# ---------------------------------------------------------------------------
# Disabled service
# ---------------------------------------------------------------------------

def test_disabled_service_passes_through():
    svc = _svc(enabled=False)
    long = _long_output(300)
    result = svc.compact(tool_name="pytest", output=long)
    assert result.compaction_ratio == 1.0
    assert result.compacted_text == long.rstrip()


# ---------------------------------------------------------------------------
# Factory from config
# ---------------------------------------------------------------------------

def test_build_from_config_defaults():
    svc = _build_from_config({})
    assert isinstance(svc, ToolOutputCompactionService)


def test_build_from_config_disabled():
    svc = _build_from_config({"enabled": False})
    result = svc.compact(tool_name="pytest", output=_long_output(300))
    assert result.compaction_ratio == 1.0


def test_build_from_none_config():
    svc = _build_from_config(None)
    assert isinstance(svc, ToolOutputCompactionService)
