from __future__ import annotations

import bz2
import gzip
import logging
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable


logger = logging.getLogger(__name__)


def _tag_local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].strip().lower()


def _open_text_lines(path: Path):
    if path.name.endswith(".bz2"):
        return bz2.open(path, "rt", encoding="utf-8", errors="replace")
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


class MediaWikiXmlDumpParser:
    def _read_offsets(self, index_path: Path) -> list[int]:
        offsets: set[int] = set()
        with _open_text_lines(index_path) as lines:
            for line in lines:
                raw = str(line or "").split(":", 1)[0].strip()
                if not raw:
                    continue
                try:
                    offsets.add(int(raw))
                except ValueError:
                    continue
        return sorted(offsets)

    def _iter_multistream_pages(self, *, corpus_path: Path, index_path: Path) -> Iterable[dict[str, Any]]:
        offsets = self._read_offsets(index_path)
        if not offsets:
            raise ValueError("wiki_multistream_index_empty")
        file_size = corpus_path.stat().st_size
        offsets = [offset for offset in offsets if 0 <= offset < file_size]
        if not offsets:
            raise ValueError("wiki_multistream_index_no_valid_offsets")
        with corpus_path.open("rb") as source:
            for position, offset in enumerate(offsets):
                next_offset = offsets[position + 1] if position + 1 < len(offsets) else file_size
                if next_offset <= offset:
                    continue
                source.seek(offset)
                compressed_block = source.read(next_offset - offset)
                if not compressed_block:
                    continue
                try:
                    xml_fragment = bz2.decompress(compressed_block)
                except OSError as exc:
                    logger.warning(
                        "Wiki multistream block could not be decompressed",
                        extra={"offset": offset, "error": str(exc)},
                    )
                    continue
                wrapped = b"<mediawiki>" + xml_fragment + b"</mediawiki>"
                context = ET.iterparse(BytesIO(wrapped), events=("end",))
                for _event, elem in context:
                    if _tag_local_name(elem.tag) != "page":
                        continue
                    yield self._parse_page(elem)
                    elem.clear()

    def _parse_page(self, elem) -> dict[str, Any]:
        redirect = elem.find(".//{*}redirect")
        redirect_title = str(redirect.attrib.get("title") or "").strip() if redirect is not None else None
        return {
            "kind": "page",
            "title": str(elem.findtext(".//{*}title") or "").strip(),
            "namespace": int(str(elem.findtext(".//{*}ns") or "0").strip() or 0),
            "text": str(elem.findtext(".//{*}revision/{*}text") or "").strip(),
            "is_redirect": bool(redirect is not None),
            "redirect_title": redirect_title or None,
        }

    def _parse_doc(self, elem) -> dict[str, Any]:
        return {
            "kind": "doc",
            "title": str(elem.findtext("./title") or "").strip(),
            "text": str(elem.findtext("./abstract") or elem.findtext("./text") or "").strip(),
        }

    def iter_items(self, *, corpus_path: Path, index_path: Path | None = None) -> Iterable[dict[str, Any]]:
        if index_path is not None:
            yield from self._iter_multistream_pages(corpus_path=corpus_path, index_path=index_path)
            return
        if corpus_path.name.endswith(".bz2"):
            raw_stream = bz2.open(corpus_path, "rb")
        elif corpus_path.name.endswith(".gz"):
            raw_stream = gzip.open(corpus_path, "rb")
        else:
            raw_stream = corpus_path.open("rb")
        with raw_stream as stream:
            context = ET.iterparse(stream, events=("end",))
            for _event, elem in context:
                local_name = _tag_local_name(elem.tag)
                if local_name == "page":
                    yield self._parse_page(elem)
                    elem.clear()
                elif local_name == "doc":
                    yield self._parse_doc(elem)
                    elem.clear()

