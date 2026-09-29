from agent.services.wiki_section_extractor import extract_wiki_sections


def test_extract_wiki_sections_splits_by_headings():
    text = "Lead text\n== A ==\nFirst\n== B ==\nSecond"
    sections = extract_wiki_sections(text=text)
    assert sections[0]["section_title"] == "Overview"
    assert sections[1]["section_title"] == "A"
    assert sections[2]["section_title"] == "B"

