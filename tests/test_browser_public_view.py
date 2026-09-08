"""Closed transient view data cannot contain HTML instructions or partial denials."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from ananta_contracts.browser_public_view import SCHEMA, blocked_view, validate_public_view
from worker.meet_media.browser_public_snapshot import PublicDocumentSnapshot


def ready():
    return {
        "schema": SCHEMA,
        "state": "ready",
        "reason": None,
        "blocks": [{"kind": "heading", "text": "Öffentliche Seite"}, {"kind": "text", "text": "Begrenzte Ansicht"}],
    }


def test_closed_view_copies_all_mutable_data_and_preserves_literal_text():
    value = ready()
    value["blocks"][1]["text"] = "<script>literal & text</script>"
    copied = validate_public_view(value)
    assert copied == value and copied is not value and copied["blocks"][1] is not value["blocks"][1]
    copied["blocks"][1]["text"] = "changed"
    assert value["blocks"][1]["text"] == "<script>literal & text</script>"


@pytest.mark.parametrize(
    "patch",
    [
        {"schema": "future"},
        {"state": True},
        {"reason": "allow"},
        {"blocks": []},
        {"blocks": ()},
        {"url": "https://private"},
        {"html": "private"},
        {"blocks": [{"kind": "html", "text": "private"}]},
        {"blocks": [{"kind": "text", "text": "private", "onclick": "execute"}]},
        {"blocks": [{"kind": "text", "text": "x"}] * 129},
        {"blocks": [{"kind": "text", "text": "x" * 500}] * 17},
    ],
)
def test_malformed_or_oversized_view_has_only_a_fixed_error(patch):
    with pytest.raises(ValueError, match="^browser_public_view_invalid$"):
        validate_public_view(ready() | patch)


@pytest.mark.parametrize(
    "text",
    [
        None,
        True,
        [],
        "",
        " leading",
        "trailing ",
        "x" * 501,
        "a\x00b",
        "a\nb",
        "a\u202eb",
        "a\u2066b",
        "\ud800",
        "O\u0308",
    ],
)
def test_text_boundary_denies_coercions_controls_bidi_surrogates_and_noncanonical_unicode(text):
    value = ready()
    value["blocks"][0]["text"] = text
    with pytest.raises(ValueError, match="^browser_public_view_invalid$"):
        validate_public_view(value)


def test_blocked_view_never_contains_partial_content_or_unbounded_reason():
    value = blocked_view("sensitive_content")
    assert validate_public_view(value) == value
    for patch in ({"blocks": ready()["blocks"]}, {"reason": "private-secret"}, {"reason": []}):
        with pytest.raises(ValueError, match="^browser_public_view_invalid$"):
            validate_public_view(value | patch)


def test_snapshot_reader_is_passive_copied_and_normalizes_transport_or_contract_failure():
    page = Mock()
    handle = page.wait_for_function.return_value
    handle.json_value.return_value = ready()
    reader = PublicDocumentSnapshot(page)
    assert reader.read() == ready()
    page.screenshot.assert_not_called()
    page.goto.assert_not_called()
    page.evaluate.assert_not_called()
    assert page.wait_for_function.call_args.kwargs == {"timeout": 750}
    handle.dispose.assert_called_once()
    handle.json_value.return_value = deepcopy(ready()) | {"private": "never-disclose"}
    assert reader.read() == blocked_view("snapshot_invalid")
    page.wait_for_function.side_effect = RuntimeError("private browser message")
    assert reader.read() == blocked_view("source_unavailable")


def test_snapshot_serialization_failure_releases_handle_and_discloses_no_partial_value():
    page = Mock()
    handle = page.wait_for_function.return_value
    handle.json_value.side_effect = RuntimeError("synthetic secret from crashed target")
    assert PublicDocumentSnapshot(page).read() == blocked_view("source_unavailable")
    handle.dispose.assert_called_once()
