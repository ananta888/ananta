from agent.sources.open_notebook_provenance import build_open_notebook_provenance


def test_known_license_and_url_are_preserved():
    result = build_open_notebook_provenance(
        {"url": "https://example.test/source", "license": "Apache-2.0", "export_version": "1"}
    )
    assert result["original_url"] == "https://example.test/source"
    assert result["license_status"] == "known"


def test_unknown_license_is_not_invented():
    result = build_open_notebook_provenance({"file_path": "docs/input.pdf", "license": "unknown"})
    assert result["original_file_path"] == "docs/input.pdf"
    assert result["license_status"] == "unknown"


def test_missing_url_remains_none():
    assert build_open_notebook_provenance({})["original_url"] is None
