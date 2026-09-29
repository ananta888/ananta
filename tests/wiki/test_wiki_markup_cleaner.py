from agent.services.wiki_markup_cleaner import clean_wiki_markup


def test_clean_wiki_markup_removes_basic_markup():
    raw = "== Intro == [[A|Alpha]] [[Kategorie:Test]] <ref>x</ref> text"
    cleaned = clean_wiki_markup(raw)
    assert "Kategorie" not in cleaned
    assert "Alpha" in cleaned
    assert "Intro" in cleaned

