from agent.services.context_evidence_model import (
    ContextEvidenceService, EvidenceType, EvidenceItem, UNCERTAIN_THRESHOLD
)
import pytest

svc = ContextEvidenceService()

def test_ast_call_confidence_is_0_9():
    e = svc.create_ast_evidence("foo.py")
    assert e.confidence == 0.9

def test_llm_assessment_confidence_is_0_3():
    e = svc.create_llm_evidence("foo.py")
    assert e.confidence == 0.3

def test_corroboration_bonus():
    e1 = svc.create_ast_evidence("a.py")
    e2 = EvidenceItem(EvidenceType.DOC_MENTION, "b.md", None, 0.5, "doc")
    conf = svc.compute_confidence([e1, e2])
    assert conf > 0.9  # max=0.9 + bonus

def test_corroboration_max_bonus():
    items = [
        EvidenceItem(EvidenceType.AST_CALL, "a.py", None, 0.9, ""),
        EvidenceItem(EvidenceType.AST_IMPORT, "b.py", None, 0.85, ""),
        EvidenceItem(EvidenceType.TEST_REFERENCE, "c.py", None, 0.8, ""),
        EvidenceItem(EvidenceType.RUNTIME_CONFIG, "d.yaml", None, 0.75, ""),
        EvidenceItem(EvidenceType.DOC_MENTION, "e.md", None, 0.5, ""),
    ]
    conf = svc.compute_confidence(items)
    assert conf <= 1.0
    assert conf >= 0.99  # 0.9 + max_bonus(0.15) = 1.05, capped to 1.0

def test_label_high():
    assert svc.label_confidence(0.75) == "high"

def test_label_uncertain():
    assert svc.label_confidence(0.25) == "uncertain"

def test_label_medium():
    assert svc.label_confidence(0.55) == "medium"

def test_uncertain_flag():
    e = svc.create_heuristic_evidence("a.py")
    ev = svc.build_evidence([e])
    # heuristic confidence = 0.35 >= 0.3 → NOT uncertain
    assert ev.is_uncertain is False

def test_uncertain_flag_llm_only():
    e = svc.create_llm_evidence("a.py")
    ev = svc.build_evidence([e])
    # 0.3 == UNCERTAIN_THRESHOLD → is_uncertain = (0.3 < 0.3) = False
    assert ev.is_uncertain is False
    assert ev.confidence_label == "low"

def test_llm_alone_cannot_exceed_0_3():
    items = [svc.create_llm_evidence("a.py"), svc.create_llm_evidence("b.py")]
    conf = svc.compute_confidence(items)
    assert conf <= 0.3

def test_default_evidence_is_heuristic():
    items = svc.default_evidence("codecompass", "foo.py")
    assert len(items) == 1
    assert items[0].evidence_type == EvidenceType.HEURISTIC

def test_summarize_mentions_count():
    e = svc.create_ast_evidence("a.py")
    ev = svc.build_evidence([e])
    summary = svc.summarize(ev)
    assert "1" in summary

def test_evidence_item_as_dict():
    e = svc.create_ast_evidence("foo.py", line=42, description="test")
    d = e.as_dict()
    assert d["evidence_type"] == "ast_call"
    assert d["line"] == 42
