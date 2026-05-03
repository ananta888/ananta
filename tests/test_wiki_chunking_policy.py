from agent.services.wiki_chunking_policy import split_wiki_content


def test_split_wiki_content_respects_max_chars():
    chunks = split_wiki_content("One. Two. Three. Four.", max_chars=8)
    assert len(chunks) >= 2
    assert all(len(item) <= 8 for item in chunks)

