"""Text arguments for decision providers that cannot write: choose among candidates (DPRV).

Decision providers such as TypeSafe Jev answer closed questions only. A tool's
text argument (e.g. a search query) is therefore asked as one more *choice*
in the same request, over candidates generated beforehand:

- ``PromptSpanCandidates`` (extractive): identifiers, paths, quoted text,
  handles and content words from the request itself;
- ``SymbolIndexCandidates``: names from a symbol index (classes, functions,
  files) that fuzzily match words of the request -- a typo or colloquial name
  becomes a name that really exists;
- ``CompositeCandidates``: merged, de-duplicated and capped.

Every question also offers ``whole_request`` (use the request as the argument,
the previous behaviour) and ``none_fits``; both fall back to the whole request.
"""

from __future__ import annotations

import difflib
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

WHOLE_REQUEST = "whole_request"
NONE_FITS = "none_fits"
MAX_CANDIDATES = 60
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_PATH = re.compile(r"(?:[\w.-]+/)+[\w.-]+")
_QUOTED = re.compile(r"[\"'`„“]([^\"'`„“]{2,80})[\"'`“”]")
_HANDLE = re.compile(r"\b[a-z]{2,6}:[A-Za-z0-9_.:/-]{3,}")
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß0-9_]{3,}")
_CAMEL = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_STOP = frozenset("""
the and for with what where which when how does this that from into über und oder der die das den dem des ein
eine einer einem wie wird werden wo was ist sind im in an auf mit von zu zum zur bitte mal zeig zeige gib erklär
erkläre show tell find explain please code datei file function funktion klasse class definiert defined gibt
kannst can could would should ich du mir mich uns es sie er wir ihr alle alles welche welcher welches
""".split())


@dataclass(frozen=True)
class Candidate:
    value: str
    origin: str  # prompt | symbol | path


class ArgumentCandidateSource(Protocol):
    def candidates(self, prompt: str, *, tool: str, argument: str) -> list[Candidate]: ...


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text) if w.lower() not in _STOP]


def split_identifier(name: str) -> list[str]:
    """``CircuitBreakerOpen`` / ``circuit_breaker_open`` -> ``["circuit", "breaker", "open"]``."""
    parts: list[str] = []
    for chunk in re.split(r"[_.\-/]+", name):
        parts.extend(p.lower() for p in _CAMEL.findall(chunk))
    return [p for p in parts if p]


class PromptSpanCandidates:
    """Spans of the request: identifiers, paths, quoted text, handles, content words and word pairs."""

    def candidates(self, prompt: str, *, tool: str = "", argument: str = "") -> list[Candidate]:
        del tool, argument
        found: list[str] = []
        found += [m.group(1).strip() for m in _QUOTED.finditer(prompt)]
        found += _HANDLE.findall(prompt) + _PATH.findall(prompt)
        found += [t for t in _IDENT.findall(prompt)
                  if ("_" in t or "." in t or re.search(r"[a-z][A-Z]", t)) and len(t) >= 3]
        words = _words(prompt)
        found += words
        found += [f"{a} {b}" for a, b in zip(words, words[1:])]
        return [Candidate(value, "prompt") for value in found]


class SymbolIndexCandidates:
    """Names of a symbol index that match words of the request (exact, part, prefix or typo-close).

    Inverted index over identifier parts: exact and part hits are dictionary lookups; typo-close matches
    are searched only in the (much smaller) vocabulary of parts, bucketed by length.
    """

    def __init__(self, names: Callable[[], Iterable[str]], *, limit: int = 40, min_ratio: float = 0.8) -> None:
        self._names_source = names
        self._limit = int(limit)
        self._min_ratio = float(min_ratio)
        self._source_ref: object = None
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        """(Re)build the inverted index when the name source returns a different list (thread-safe)."""
        names = self._names_source()
        if names is self._source_ref:
            return
        with self._lock:
            if names is not self._source_ref:
                self._build(names)
                self._source_ref = names  # published only once the index is complete

    def _build(self, source: Iterable[str]) -> None:
        self._names: list[str] = []
        self._exact: dict[str, set[int]] = {}
        self._parts: dict[str, set[int]] = {}
        for index, name in enumerate(n for n in dict.fromkeys(source) if n):
            self._names.append(name)
            parts = split_identifier(name)
            base = name.lower().rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for key in {name.lower(), base, "".join(parts)}:
                self._exact.setdefault(key, set()).add(index)
            for part in set(parts):
                self._parts.setdefault(part, set()).add(index)
        self._by_length: dict[int, list[str]] = {}
        for part in self._parts:
            self._by_length.setdefault(len(part), []).append(part)

    def _word_hits(self, word: str) -> dict[int, float]:
        hits: dict[int, float] = {}

        def add(indices: Iterable[int], score: float) -> None:
            for index in indices:
                if hits.get(index, 0.0) < score:
                    hits[index] = score
        add(self._exact.get(word, ()), 3.0)
        add(self._parts.get(word, ()), 2.0)
        if len(word) >= 4:
            for length in range(len(word) - 2, len(word) + 3):
                for part in self._by_length.get(length, ()):
                    if part == word:
                        continue
                    if len(word) >= 5 and part.startswith(word):
                        add(self._parts[part], 1.5)
                        continue
                    matcher = difflib.SequenceMatcher(None, word, part)
                    if matcher.real_quick_ratio() >= self._min_ratio and matcher.quick_ratio() >= self._min_ratio:
                        ratio = matcher.ratio()
                        if ratio >= self._min_ratio:
                            add(self._parts[part], ratio)
        return hits

    def candidates(self, prompt: str, *, tool: str = "", argument: str = "") -> list[Candidate]:
        del tool, argument
        self._refresh()
        raw = _words(prompt)
        words = [w.lower() for w in raw]
        words += [part for w in raw for part in split_identifier(w) if len(part) >= 3 and part not in _STOP]
        words += ["".join(pair) for pair in zip(words, words[1:])]  # "circuit breaker" -> "circuitbreaker"
        per_name: dict[int, list[float]] = {}
        for word in dict.fromkeys(words):
            for index, score in self._word_hits(word).items():
                per_name.setdefault(index, []).append(score)
        scored = sorted(((sum(sorted(v, reverse=True)[:3]), -len(self._names[i]), self._names[i])
                         for i, v in per_name.items()), reverse=True)
        return [Candidate(name, "path" if "/" in name else "symbol") for _s, _l, name in scored[: self._limit]]


class CompositeCandidates:
    def __init__(self, sources: Sequence[ArgumentCandidateSource], *, limit: int = MAX_CANDIDATES) -> None:
        self._sources = tuple(sources)
        self._limit = int(limit)

    def candidates(self, prompt: str, *, tool: str = "", argument: str = "") -> list[Candidate]:
        seen: set[str] = set()
        result: list[Candidate] = []
        # interleave the sources so neither crowds out the other
        streams = [source.candidates(prompt, tool=tool, argument=argument) for source in self._sources]
        for index in range(max((len(s) for s in streams), default=0)):
            for stream in streams:
                if index < len(stream):
                    candidate = stream[index]
                    key = candidate.value.strip().lower()
                    if key and key not in seen and len(candidate.value) <= 200:
                        seen.add(key)
                        result.append(candidate)
        return result[: self._limit]


# --- symbol names from source text ------------------------------------------------------------------

_DEFINITIONS = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|struct|enum|trait|fn|func|type)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def symbol_names(files: Iterable[tuple[str, str]]) -> list[str]:
    """Definition names and file paths from ``(path, content)`` pairs (Python, JS/TS, Java, Go, Rust)."""
    names: list[str] = []
    for path, content in files:
        names.append(path)
        names.extend(_DEFINITIONS.findall(content or ""))
    return list(dict.fromkeys(names))


# --- default source: the request plus the Hub's current knowledge index ---------------------------------------

_INDEX_CACHE: dict[str, tuple[float, list[str]]] = {}


def index_symbol_names() -> list[str]:
    """Symbol names of the newest completed knowledge index this process can read (empty when none)."""
    import json
    from pathlib import Path

    try:
        from agent.services.repository_registry import get_repository_registry

        indices = list(get_repository_registry().knowledge_index_repo.list_completed() or [])
    except Exception:  # noqa: BLE001 -- no index means prompt candidates only
        return []
    for index in sorted(indices, key=lambda i: float(getattr(i, "updated_at", 0) or 0), reverse=True):
        path = Path(str(getattr(index, "output_dir", "") or "")) / "index.jsonl"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        cached = _INDEX_CACHE.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        files = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                files.append((str(row.get("path") or row.get("file") or ""), str(row.get("content") or "")))
        names = symbol_names(files)
        _INDEX_CACHE[str(path)] = (mtime, names)
        return names
    return []


_DEFAULT: CompositeCandidates | None = None


def default_candidate_source() -> CompositeCandidates:
    """Request spans plus the knowledge index's names; one instance, rebuilt only when the index changes."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CompositeCandidates([PromptSpanCandidates(), SymbolIndexCandidates(index_symbol_names)])
    return _DEFAULT
