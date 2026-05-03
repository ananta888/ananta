from agent.services.wiki_semantic_extractor import extract_wiki_semantic_signals


def test_extract_wiki_semantic_signals_links_and_categories():
    signals = extract_wiki_semantic_signals("[[Berlin]] [[Kategorie:Deutschland]] [[A|B]]")
    assert "Berlin" in signals["links"]
    assert "A" in signals["links"]
    assert "Deutschland" in signals["categories"]

