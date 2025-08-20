import pytest

from src.models.hrm import HierarchicalReasoningModel, HRMConfig


def test_hrm_addition_basic():
    hrm = HierarchicalReasoningModel(HRMConfig(max_macro_steps=3, max_micro_steps=10, trace=True))
    text = "Please add 2 and 3 and 5."
    out = hrm.forward(text)
    assert out["objective"] == "add"
    assert out["answer"] == 10
    # trace sanity
    tr = out.get("trace", {})
    assert isinstance(tr, dict)
    assert isinstance(tr.get("high_level", []), list)
    assert isinstance(tr.get("low_level", []), list)
    assert len(tr["high_level"]) >= 1
    assert len(tr["low_level"]) >= 1


def test_hrm_multiplication_basic():
    hrm = HierarchicalReasoningModel(HRMConfig(max_macro_steps=3, max_micro_steps=10, trace=True))
    text = "Multiply 4 by 5 and 2"
    out = hrm.forward(text)
    assert out["objective"] == "multiply"
    assert out["answer"] == 40


def test_hrm_unknown_objective_uses_first_number():
    hrm = HierarchicalReasoningModel(HRMConfig(max_macro_steps=2, max_micro_steps=5, trace=False))
    text = "Numbers: 7, 8, 9"
    out = hrm.forward(text)
    assert out["objective"] == "unknown"
    assert out["answer"] == 7


def test_hrm_trace_structure_and_content():
    hrm = HierarchicalReasoningModel(HRMConfig(trace=True))
    text = "sum 10, 20, and 7"
    out = hrm(text)  # __call__ alias
    tr = out["trace"]
    # high level steps contain objective and segments
    assert any("objective" in step and "segments" in step for step in tr["high_level"]) 
    # low level steps contain buffer growth and last_segment
    assert any("buffer" in step and "last_segment" in step for step in tr["low_level"]) 
    assert out["answer"] == 37
