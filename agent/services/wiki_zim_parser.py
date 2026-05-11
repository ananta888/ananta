"""
Minimal ZIM v5/v6 parser (prototype).

Yields items in the same format as MediaWikiXmlDumpParser so the standard
WikiRecordNormalizer and WikiRecordWriter pipelines work unchanged.

Unsupported features (LZ4 compression, unknown compression types) are
collected in .issues rather than causing silent failures or crashes.

ZSTD decompression (the dominant compression in modern ZIM files) requires
the optional `zstandard` package.  Without it, ZSTD clusters are skipped and
the issue "zstd_compression_requires_zstandard_package" is reported.
"""

from __future__ import annotations

import html
import logging
import re
import struct
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_ZIM_MAGIC = 0x044D495A

_MIME_REDIRECT = 0xFFFF
_MIME_DELETED = 0xFFFE

_COMPRESS_NONE = frozenset((0, 1))
_COMPRESS_ZSTD = 4
_COMPRESS_LZ4 = 5

# fmt: off
_HEADER = struct.Struct("<IHH16sIIQQQQIIQ")  # 80 bytes
# I  magic
# H  majorVersion
# H  minorVersion
# 16s uuid
# I  articleCount
# I  clusterCount
# Q  urlPtrPos
# Q  titlePtrPos
# Q  clusterPtrPos
# Q  mimeListPos
# I  mainPage
# I  layoutPage
# Q  checksumPos

_ENTRY_BASE    = struct.Struct("<HBBI")  # mimeTypeIndex, paramLen, namespace_byte, revision
_ENTRY_ARTICLE = struct.Struct("<II")   # clusterNumber, blobNumber
_ENTRY_REDIRECT = struct.Struct("<I")   # redirectIndex
# fmt: on

# ZIM article namespaces that map to MediaWiki namespace 0
_ARTICLE_NAMESPACES = frozenset(("A", "C"))


def _strip_html(text: str) -> str:
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _read_cstring(f: Any) -> str:
    buf = bytearray()
    while True:
        ch = f.read(1)
        if not ch or ch == b"\x00":
            return buf.decode("utf-8", errors="replace")
        buf += ch


class ZimParser:
    """
    Pure-Python ZIM reader that implements the WikiDumpParser protocol.

    Usage::

        parser = ZimParser()
        for item in parser.iter_items(corpus_path=Path("wikipedia.zim")):
            # item has the same shape as MediaWikiXmlDumpParser output
            print(item["title"], item["text"][:80])
        if parser.issues:
            print("Unsupported features:", parser.issues)
    """

    def __init__(self) -> None:
        self._issues: list[str] = []

    @property
    def issues(self) -> list[str]:
        return list(self._issues)

    def _report(self, issue: str) -> None:
        if issue not in self._issues:
            self._issues.append(issue)

    # ------------------------------------------------------------------
    # Header and directory
    # ------------------------------------------------------------------

    def _read_header(self, f: Any) -> dict[str, Any]:
        data = f.read(_HEADER.size)
        if len(data) < _HEADER.size:
            raise ValueError("zim_file_too_small")
        (
            magic, major, minor, uuid,
            article_count, cluster_count,
            url_ptr_pos, title_ptr_pos, cluster_ptr_pos, mime_list_pos,
            main_page, layout_page, checksum_pos,
        ) = _HEADER.unpack(data)
        if magic != _ZIM_MAGIC:
            raise ValueError(f"not_a_zim_file:magic={magic:#010x}")
        if major not in (5, 6):
            self._report(f"unsupported_zim_major_version:{major}")
        return {
            "major": major,
            "minor": minor,
            "uuid": uuid,
            "article_count": article_count,
            "cluster_count": cluster_count,
            "url_ptr_pos": url_ptr_pos,
            "title_ptr_pos": title_ptr_pos,
            "cluster_ptr_pos": cluster_ptr_pos,
            "mime_list_pos": mime_list_pos,
            "main_page": main_page,
            "layout_page": layout_page,
            "checksum_pos": checksum_pos,
        }

    def _read_mime_list(self, f: Any, mime_list_pos: int) -> list[str]:
        f.seek(mime_list_pos)
        mimes: list[str] = []
        while True:
            s = _read_cstring(f)
            if not s:
                break
            mimes.append(s)
        return mimes

    def _read_ptr_list_64(self, f: Any, pos: int, count: int) -> list[int]:
        if count == 0:
            return []
        f.seek(pos)
        data = f.read(count * 8)
        return list(struct.unpack_from(f"<{count}Q", data))

    def _read_dir_entry(self, f: Any, offset: int) -> dict[str, Any]:
        f.seek(offset)
        base_data = f.read(_ENTRY_BASE.size)
        mime_idx, param_len, ns_byte, revision = _ENTRY_BASE.unpack(base_data)
        namespace = chr(ns_byte)
        is_redirect = mime_idx == _MIME_REDIRECT
        is_deleted = mime_idx == _MIME_DELETED

        if is_redirect:
            extra_data = f.read(_ENTRY_REDIRECT.size)
            redirect_idx = _ENTRY_REDIRECT.unpack(extra_data)[0]
            url = _read_cstring(f)
            title = _read_cstring(f) or url
            if param_len:
                f.read(param_len)
            return {
                "mime_idx": mime_idx, "namespace": namespace,
                "is_redirect": True, "is_deleted": False,
                "redirect_idx": redirect_idx, "url": url, "title": title,
            }

        if is_deleted:
            url = _read_cstring(f)
            title = _read_cstring(f) or url
            if param_len:
                f.read(param_len)
            return {
                "mime_idx": mime_idx, "namespace": namespace,
                "is_redirect": False, "is_deleted": True,
                "url": url, "title": title,
            }

        extra_data = f.read(_ENTRY_ARTICLE.size)
        cluster_number, blob_number = _ENTRY_ARTICLE.unpack(extra_data)
        url = _read_cstring(f)
        title = _read_cstring(f) or url
        if param_len:
            f.read(param_len)
        return {
            "mime_idx": mime_idx, "namespace": namespace,
            "is_redirect": False, "is_deleted": False,
            "cluster_number": cluster_number, "blob_number": blob_number,
            "url": url, "title": title,
        }

    # ------------------------------------------------------------------
    # Cluster / blob reading
    # ------------------------------------------------------------------

    def _read_blob(
        self,
        f: Any,
        cluster_offset: int,
        blob_number: int,
        next_cluster_offset: int,
    ) -> bytes | None:
        f.seek(cluster_offset)
        compress_byte = f.read(1)
        if not compress_byte:
            return None
        c = compress_byte[0]
        compress_type = c & 0x0F
        extended = bool(c & 0x10)
        offset_size = 8 if extended else 4
        offset_fmt = "Q" if extended else "I"

        if compress_type in _COMPRESS_NONE:
            first_raw = f.read(offset_size)
            if len(first_raw) < offset_size:
                return None
            first_offset = struct.unpack(f"<{offset_fmt}", first_raw)[0]
            n_blobs = first_offset // offset_size - 1
            if blob_number >= n_blobs:
                self._report(f"blob_number_out_of_range:{blob_number}>={n_blobs}")
                return None
            f.seek(cluster_offset + 1 + blob_number * offset_size)
            pair_raw = f.read(offset_size * 2)
            if len(pair_raw) < offset_size * 2:
                return None
            start, end = struct.unpack(f"<{offset_fmt}{offset_fmt}", pair_raw)
            if end <= start:
                return b""
            f.seek(cluster_offset + 1 + start)
            return f.read(end - start)

        if compress_type == _COMPRESS_ZSTD:
            try:
                import zstandard  # type: ignore[import-untyped]
            except ImportError:
                self._report("zstd_compression_requires_zstandard_package")
                return None
            cluster_data_size = next_cluster_offset - cluster_offset - 1
            compressed = f.read(cluster_data_size)
            try:
                decompressed = zstandard.ZstdDecompressor().decompress(
                    compressed, max_output_size=256 * 1024 * 1024
                )
            except Exception as exc:
                self._report(f"zstd_decompression_failed:{exc}")
                return None
            first_offset = struct.unpack_from(f"<{offset_fmt}", decompressed, 0)[0]
            n_blobs = first_offset // offset_size - 1
            if blob_number >= n_blobs:
                self._report(f"blob_number_out_of_range:{blob_number}>={n_blobs}")
                return None
            start, end = struct.unpack_from(
                f"<{offset_fmt}{offset_fmt}", decompressed, blob_number * offset_size
            )
            return decompressed[start:end]

        if compress_type == _COMPRESS_LZ4:
            self._report("lz4_compression_not_supported")
            return None

        self._report(f"unknown_compression_type:{compress_type}")
        return None

    # ------------------------------------------------------------------
    # Public interface (WikiDumpParser protocol)
    # ------------------------------------------------------------------

    def iter_items(
        self,
        *,
        corpus_path: Path,
        index_path: Path | None = None,
    ) -> Iterable[dict[str, Any]]:
        """
        Stream article items from a ZIM file.

        Each yielded dict has the shape::

            {
                "kind": "page",
                "title": str,
                "namespace": int,   # 0 for articles, -1 for other ZIM namespaces
                "text": str,        # plain text (HTML stripped)
                "is_redirect": bool,
                "redirect_title": str | None,
            }
        """
        self._issues = []
        if index_path is not None:
            self._report("zim_index_path_ignored:zim_is_self_contained")
        file_size = corpus_path.stat().st_size
        with corpus_path.open("rb") as f:
            header = self._read_header(f)
            mime_list = self._read_mime_list(f, header["mime_list_pos"])
            url_ptrs = self._read_ptr_list_64(f, header["url_ptr_pos"], header["article_count"])
            cluster_ptrs = self._read_ptr_list_64(f, header["cluster_ptr_pos"], header["cluster_count"])

            for entry_offset in url_ptrs:
                entry = self._read_dir_entry(f, entry_offset)

                if entry["is_deleted"]:
                    continue

                ns = entry["namespace"]
                title = entry["title"]
                mw_namespace = 0 if ns in _ARTICLE_NAMESPACES else -1

                if entry["is_redirect"]:
                    redirect_idx = entry.get("redirect_idx", 0)
                    redirect_title: str | None = None
                    if 0 <= redirect_idx < len(url_ptrs):
                        try:
                            target = self._read_dir_entry(f, url_ptrs[redirect_idx])
                            redirect_title = target["title"] or None
                        except Exception as exc:
                            logger.debug("Failed to resolve redirect target: %s", exc)
                    yield {
                        "kind": "page",
                        "title": title,
                        "namespace": mw_namespace,
                        "text": "",
                        "is_redirect": True,
                        "redirect_title": redirect_title,
                    }
                    continue

                mime_idx = entry["mime_idx"]
                if mime_idx < len(mime_list):
                    mime = mime_list[mime_idx]
                else:
                    self._report(f"unknown_mime_index:{mime_idx}")
                    mime = ""

                cluster_number = entry["cluster_number"]
                blob_number = entry["blob_number"]

                if cluster_number >= len(cluster_ptrs):
                    self._report(f"invalid_cluster_number:{cluster_number}")
                    continue

                cluster_offset = cluster_ptrs[cluster_number]
                next_cluster_offset = (
                    cluster_ptrs[cluster_number + 1]
                    if cluster_number + 1 < len(cluster_ptrs)
                    else file_size
                )

                raw = self._read_blob(f, cluster_offset, blob_number, next_cluster_offset)
                if raw is None:
                    continue

                text = raw.decode("utf-8", errors="replace")
                if "html" in mime.lower():
                    text = _strip_html(text)

                if not text.strip():
                    continue

                yield {
                    "kind": "page",
                    "title": title,
                    "namespace": mw_namespace,
                    "text": text,
                    "is_redirect": False,
                    "redirect_title": None,
                }
