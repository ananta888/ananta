from agent.sources.source_transformation_templates import (
    get_source_transformation_template,
    list_source_transformation_templates,
)


def test_templates_are_complete_unique_and_stably_sorted():
    templates = list_source_transformation_templates()
    ids = [item["id"] for item in templates]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids)) == 7
    assert set(ids) == {
        "summary",
        "key_terms",
        "architecture_facts",
        "api_contracts",
        "security_notes",
        "testable_claims",
        "citation_candidates",
    }
    assert all(item["allowed_source_types"] == ["open_notebook"] for item in templates)
    assert get_source_transformation_template("summary")["title"] == "Summary"
