from __future__ import annotations

from scripts.check_spreadsheet_studio_research_pack import validate_pack
from scripts.spreadsheet_studio_research_pack_support import (
    build_authoritative_catalog,
    resolve_persisted_catalog,
)


def test_spreadsheet_studio_research_pack_is_grounded_and_promoted() -> None:
    result = validate_pack()

    assert result["status"] == "passed"
    assert result["research_items"] == 33
    assert result["verified_claims"] == 34
    assert result["promotion"] == "automatic_policy_promoted"
    assert result["human_in_loop_tests"] == "forbidden"


def test_spreadsheet_studio_catalog_resolves_from_persisted_hub_projection() -> None:
    catalog, _publication = build_authoritative_catalog()
    resolved = resolve_persisted_catalog(catalog)

    assert resolved["catalog_id"] == catalog["catalog_id"]
    assert resolved["catalog_hash"] == catalog["catalog_hash"]
    assert len(resolved["source_refs"]) == 14
