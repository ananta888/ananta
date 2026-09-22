"""CodeCompass as a model-callable tool (OpenAI function calling).

The companion used to decide for the model whether a question needed project
knowledge and prepended the retrieved snippets to every prompt. Here the chat
model is offered one function instead — ``codecompass_search`` — and decides
for itself when to look something up.

Everything a tool round can cost is bounded in this module: the query length,
the number of snippets and the size of the returned text block. Only the query
leaves the worker; the knowledge index and the worker key stay behind the Hub
(``assist.fetch_snippets``), and a failed lookup becomes a short, harmless
sentence for the model rather than an exception that loses the turn.
"""

import json
import os

NAME = "codecompass_search"

MAX_QUERY_CHARS = 200
MAX_LIMIT = 8
DEFAULT_LIMIT = 5
MAX_CALLS_PER_REPLY = 4

EMPTY_RESULT = "codecompass_search: keine passende Stelle im Wissensindex gefunden."
FAILED_RESULT = "codecompass_search: der Wissensindex ist gerade nicht erreichbar."
MISSING_QUERY = "codecompass_search: Aufruf ohne 'query' – bitte mit einem Suchbegriff erneut aufrufen."
UNKNOWN_TOOL = "unbekanntes Werkzeug – nur codecompass_search steht zur Verfügung."

DEFINITION = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Durchsucht den Ananta-Wissensindex (CodeCompass) über Repository-Code und Doku "
            "und liefert kurze Auszüge mit Dateipfad. Aufrufen, wenn es um Ananta, dieses "
            "Projekt, seinen Code, seine Architektur oder die eigene Funktionsweise geht."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff oder Frage, z. B. 'Machine Trust' oder 'Lippensync Pipeline'.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": "Höchstzahl der Auszüge (Standard %d)." % DEFAULT_LIMIT,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


def max_result_chars():
    """Size of one tool result block; the same budget the RAG prefix used."""
    return max(0, int(os.environ.get("MEET_LLM_TOOL_RESULT_CHARS", "1400")))


def max_rounds():
    """Tool rounds a single reply may spend before the model must answer."""
    return max(0, min(4, int(os.environ.get("MEET_LLM_TOOL_ROUNDS", "2"))))


def parse_arguments(raw):
    """Model-supplied arguments as a dict; anything unusable becomes ``{}``.

    OpenAI servers send a JSON string, Ollama sends an object — both arrive
    here, and neither is trusted: a malformed payload is not an error, it is
    an empty argument set the tool answers with a usage hint.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw[:4096])
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class CodeCompassTool:
    """Executes ``codecompass_search`` against an injected snippet retriever."""

    name = NAME
    definition = DEFINITION

    def __init__(self, retriever=None, *, max_chars=None):
        """``retriever(query, limit)`` -> snippets; ``None`` uses the Hub client."""
        self._retriever = retriever
        self._max_chars = max_chars
        self.calls = []
        self.sources = []

    def reset(self):
        """Forget the previous reply's calls: the trace is per message."""
        self.calls = []
        self.sources = []

    def run(self, arguments):
        """Return one bounded text block; never raises, never leaks a reason."""
        arguments = parse_arguments(arguments)
        query = " ".join(str(arguments.get("query") or "").split())[:MAX_QUERY_CHARS]
        limit = _limit(arguments.get("limit"))
        if not query:
            return MISSING_QUERY
        if len(self.calls) >= MAX_CALLS_PER_REPLY:
            return EMPTY_RESULT
        try:
            snippets = [item for item in (self._retrieve(query, limit) or []) if isinstance(item, dict)]
        except Exception:  # noqa: BLE001 -- an unreachable index is a tool result, not a lost turn
            self.calls.append({"query": query, "limit": limit, "snippets": 0, "failed": True})
            return FAILED_RESULT
        self.calls.append({"query": query, "limit": limit, "snippets": len(snippets), "failed": False})
        self.sources.extend(snippets[:MAX_LIMIT])
        from worker.meet_media.companion_dialog import context_block

        budget = max_result_chars() if self._max_chars is None else int(self._max_chars)
        block = context_block(snippets, max_chars=budget)
        return block or EMPTY_RESULT

    def _retrieve(self, query, limit):
        if self._retriever is not None:
            return self._retriever(query, limit)
        from worker.meet_media.assist import fetch_snippets

        return fetch_snippets(query, limit=limit)


def _limit(value):
    # ``type(...) is not int`` also rejects ``True``, which JSON allows here.
    if type(value) is not int:
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


class ToolBox:
    """The tools offered to one model, plus what they were asked this reply."""

    def __init__(self, tools):
        self._tools = {tool.name: tool for tool in tools}

    def definitions(self):
        return [tool.definition for tool in self._tools.values()]

    def run(self, name, arguments):
        """Execute a model-chosen call; an unknown name is answered, not raised."""
        tool = self._tools.get(name if isinstance(name, str) else "")
        if tool is None:
            return UNKNOWN_TOOL
        return str(tool.run(arguments))[: max_result_chars() or 1]

    def reset(self):
        for tool in self._tools.values():
            tool.reset()

    @property
    def calls(self):
        return [call for tool in self._tools.values() for call in tool.calls]

    @property
    def sources(self):
        return [source for tool in self._tools.values() for source in tool.sources]


def codecompass_toolbox(retriever=None):
    """The companion's tool set: CodeCompass, and nothing else."""
    return ToolBox([CodeCompassTool(retriever)])
