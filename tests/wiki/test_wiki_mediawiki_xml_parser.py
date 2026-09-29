import bz2

from agent.services.wiki_mediawiki_xml_parser import MediaWikiXmlDumpParser


def test_iter_mediawiki_pages_parses_bz2(tmp_path):
    xml = (
        "<mediawiki>"
        "<page><title>A</title><ns>0</ns><revision><text>Body</text></revision></page>"
        "<page><title>B</title><ns>1</ns><redirect title='X'/><revision><text>#REDIRECT [[X]]</text></revision></page>"
        "</mediawiki>"
    )
    path = tmp_path / "wiki.xml.bz2"
    path.write_bytes(bz2.compress(xml.encode("utf-8")))
    pages = list(MediaWikiXmlDumpParser().iter_items(corpus_path=path))
    assert len(pages) == 2
    assert pages[0]["title"] == "A"
    assert pages[0]["namespace"] == 0
    assert pages[1]["is_redirect"] is True
