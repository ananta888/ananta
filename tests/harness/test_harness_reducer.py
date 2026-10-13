"""SEH-007: evidence-preserving reducer fail-closed verification."""

from __future__ import annotations

from agent.services.harness_reducer import EvidencePreservingReducer

ORIGINAL = "start\nERROR: disk full at line 12\nnote: retry scheduled\nend\n"


def test_verbatim_selection_reduces_and_verifies_spans():
    reducer = EvidencePreservingReducer()
    reduction = reducer.reduce(ORIGINAL, select=EvidencePreservingReducer.select_by_markers(
        ["ERROR: disk full at line 12", "note: retry scheduled"]))
    assert reduction.fallback is False and reduction.reason is None
    assert reduction.reduced_text == "ERROR: disk full at line 12\nnote: retry scheduled"
    assert all(ORIGINAL[span.source_start:span.source_end] == span.text for span in reduction.spans)
    assert reduction.saved_tokens > 0


def test_non_verbatim_quote_falls_back_to_original():
    reducer = EvidencePreservingReducer()

    def liar(text):
        return [(0, 5, "WRONG")]

    reduction = reducer.reduce(ORIGINAL, select=liar)
    assert reduction.fallback is True and reduction.reason == "reducer_span_not_verbatim"
    assert reduction.reduced_text == ORIGINAL and reduction.spans == ()


def test_empty_selection_falls_back():
    reduction = EvidencePreservingReducer().reduce(ORIGINAL, select=lambda _text: [])
    assert reduction.fallback and reduction.reason == "reducer_no_evidence"
    assert reduction.reduced_text == ORIGINAL


def test_out_of_bounds_span_falls_back():
    reduction = EvidencePreservingReducer().reduce(ORIGINAL, select=lambda _t: [(0, len(ORIGINAL) + 1, "x")])
    assert reduction.fallback and reduction.reason == "reducer_span_bounds_invalid"


def test_selector_error_falls_back_closed():
    def boom(_text):
        raise RuntimeError("selector bug")

    reduction = EvidencePreservingReducer().reduce(ORIGINAL, select=boom)
    assert reduction.fallback and reduction.reason == "reducer_selector_error"
    assert reduction.reduced_text == ORIGINAL


def test_adversarial_near_identical_markers_are_verified_exactly():
    text = "value=100\nvalue=1000\n"
    # The marker "value=100" appears as a prefix of "value=1000"; the selector
    # must return the exact occurrence, not a fuzzy match.
    reduction = EvidencePreservingReducer().reduce(
        text, select=EvidencePreservingReducer.select_by_markers(["value=100"]))
    assert reduction.spans[0].source_start == 0 and reduction.spans[0].source_end == 9
    assert text[reduction.spans[0].source_start:reduction.spans[0].source_end] == "value=100"


def test_contradictory_claim_is_not_accepted():
    text = "status: healthy\nstatus: degraded\n"
    reducer = EvidencePreservingReducer()

    def contradiction(_text):
        # A reducer that claims a span text the source does not contain at that offset.
        return [(8, 22, "status: degraded")]

    reduction = reducer.reduce(text, select=contradiction)
    # 8:22 is not the degraded segment, so verification fails closed.
    assert reduction.fallback is True
