"""CodeCompass as model-callable tools (OpenAI function calling).

The companion used to decide for the model whether a question needed project
knowledge and prepended the retrieved snippets to every prompt. Here the chat
model is offered functions instead and decides for itself when to look
something up: ``codecompass_search`` (the Hub's ``codecompass.retrieve``) plus
the other read-only CodeCompass MCP tools — architecture overview/expand,
architecture intelligence, layer heads/plan, analytics templates and the
recursive analysis (``HUB_TOOL_SPECS``). They run on the Hub through the
worker-key route ``/internal/assist/tool``; the worker never talks MCP and
holds no user or backend credential.

Everything a tool round can cost is bounded in this module: the query length,
the number of snippets and the size of the returned text block. Only the query
leaves the worker; the knowledge index and the worker key stay behind the Hub
(``assist.fetch_snippets``), and a failed lookup becomes a short, harmless
sentence for the model rather than an exception that loses the turn.

For knowledge questions the router does not leave the call to the model:
``ToolBox.force`` runs ``codecompass_search`` before the first request and the
backends replay it as an ordinary tool round (``forced_messages``), so the
model always answers with the result in front of it and may still refine it.
"""

import json
import os
import re

NAME = "codecompass_search"

MAX_QUERY_CHARS = 200
MAX_LIMIT = 8
DEFAULT_LIMIT = 5
MAX_CALLS_PER_REPLY = 4

EMPTY_RESULT = "codecompass_search: keine passende Stelle im Wissensindex gefunden."
FAILED_RESULT = "codecompass_search: der Wissensindex ist gerade nicht erreichbar."
MISSING_QUERY = "codecompass_search: Aufruf ohne 'query' – bitte mit einem Suchbegriff erneut aufrufen."
UNKNOWN_TOOL = "unbekanntes Werkzeug – nur die angebotenen CodeCompass-Werkzeuge stehen zur Verfügung."
BUDGET_EXHAUSTED = (
    "Werkzeugbudget für diese Antwort erschöpft – antworte jetzt mit den vorliegenden Ergebnissen."
)

# Model-facing names use ``_`` (OpenAI function names allow no dots); the
# dotted MCP names and ``codecompass_retrieve`` are accepted as aliases, so a
# model that writes the MCP name still reaches the right tool.
ALIASES = {"codecompass_retrieve": NAME, "codecompass.retrieve": NAME}

# Sent as the last user turn of the tool-free final request. Withdrawing the
# tools alone is not enough: a model that has just seen several tool rounds
# imitates them and writes the next call as plain ``<tool_call>`` text.
FINAL_ANSWER = (
    "Keine weiteren Werkzeugaufrufe. Antworte jetzt direkt als Text auf die Frage, "
    "gestützt auf die Suchergebnisse oben."
)

_LEAKED_BLOCK = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.IGNORECASE | re.DOTALL)
_LEAKED_FUNCTION = re.compile(r"<function=([A-Za-z0-9_.-]{1,64})>(.*?)(?:</function>|$)", re.IGNORECASE | re.DOTALL)
_LEAKED_PARAMETER = re.compile(r"<parameter=([A-Za-z0-9_]{1,32})>(.*?)(?:</parameter>|$)", re.IGNORECASE | re.DOTALL)

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


def leaked_calls(text):
    """Tool calls a model wrote as plain text instead of ``tool_calls``.

    Two shapes are recognised inside ``<tool_call>`` blocks: the XML style
    (``<function=name><parameter=query>…``) and the JSON style
    (``{"name": …, "arguments": {…}}``). Returns ``[(name, arguments)]``,
    bounded like a structured reply; anything else yields ``[]``.
    """
    text = str(text or "")[:4096]
    if "<tool_call" not in text.lower():
        return []
    calls = []
    for block in _LEAKED_BLOCK.findall(text):
        function = _LEAKED_FUNCTION.search(block)
        if function is not None:
            arguments = {}
            for key, value in _LEAKED_PARAMETER.findall(function.group(2)):
                value = " ".join(value.split())
                arguments[key] = int(value) if value.isdigit() else value
            calls.append((function.group(1), arguments))
        else:
            parsed = parse_arguments(block.strip())
            if isinstance(parsed.get("name"), str) and parsed["name"]:
                calls.append((parsed["name"][:64], parse_arguments(parsed.get("arguments"))))
        if len(calls) >= MAX_CALLS_PER_REPLY:
            break
    return calls


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


def max_total_calls():
    """Tool executions one reply may spend across all tools (``MEET_LLM_TOOL_CALLS``)."""
    try:
        value = int(os.environ.get("MEET_LLM_TOOL_CALLS", "6"))
    except ValueError:
        value = 6
    return max(1, min(16, value))


class ToolBox:
    """The tools offered to one model, plus what they were asked this reply."""

    def __init__(self, tools):
        self._tools = {tool.name: tool for tool in tools}
        self.forced = []

    def definitions(self):
        return [tool.definition for tool in self._tools.values()]

    def _resolve(self, name):
        if not isinstance(name, str):
            return None
        name = name.strip()
        tool = self._tools.get(name)
        if tool is None:
            tool = self._tools.get(ALIASES.get(name) or HUB_TOOL_ALIASES.get(name, ""))
        return tool

    def run(self, name, arguments):
        """Execute a model-chosen call; an unknown name is answered, not raised."""
        tool = self._resolve(name)
        if tool is None:
            return UNKNOWN_TOOL
        if len(self.calls) >= max_total_calls():
            return BUDGET_EXHAUSTED
        return str(tool.run(arguments))[: max_result_chars() or 1]

    def force(self, name, arguments):
        """Run a call the router decided on; the backends replay it as round zero."""
        if not isinstance(arguments, dict):
            arguments = {}
        content = self.run(name, arguments)
        tool = self._resolve(name)
        if tool is not None and tool.calls:
            tool.calls[-1]["forced"] = True
        self.forced.append(
            {
                "id": "forced-%d" % len(self.forced),
                "name": str(name or "")[:64],
                "arguments": arguments,
                "content": content,
            }
        )
        return content

    def forced_messages(self, *, json_arguments):
        """The forced calls as an assistant ``tool_calls`` turn plus its results.

        OpenAI servers expect the arguments as a JSON string and correlate by
        id; Ollama takes an object and correlates by name.
        """
        if not self.forced:
            return []
        calls, results = [], []
        for call in self.forced:
            arguments = json.dumps(call["arguments"], ensure_ascii=False) if json_arguments else call["arguments"]
            entry = {"type": "function", "function": {"name": call["name"], "arguments": arguments}}
            result = {"role": "tool", "name": call["name"], "content": call["content"]}
            if json_arguments:
                entry["id"] = result["tool_call_id"] = call["id"]
            calls.append(entry)
            results.append(result)
        return [{"role": "assistant", "content": "", "tool_calls": calls}, *results]

    def reset(self):
        self.forced = []
        for tool in self._tools.values():
            tool.reset()

    @property
    def calls(self):
        return [call for tool in self._tools.values() for call in tool.calls]

    @property
    def sources(self):
        return [source for tool in self._tools.values() for source in tool.sources]


def codecompass_toolbox(retriever=None, hub=None, *, toolset=None):
    """The companion's tool set: CodeCompass, and nothing else.

    ``retriever`` backs ``codecompass_search``; ``hub(mcp_name, arguments)``
    backs the other read-only CodeCompass tools (``assist.call_tool``). Without
    a ``hub`` port, or with ``MEET_LLM_TOOLSET=search``, only the search is
    offered — the previous single-tool behaviour.
    """
    tools = [CodeCompassTool(retriever)]
    toolset = (toolset if toolset is not None else os.environ.get("MEET_LLM_TOOLSET", "all")).strip().lower()
    if hub is not None and toolset != "search":
        memory = ToolMemory()
        tools.extend(HubTool(spec, hub, memory) for spec in HUB_TOOL_SPECS)
    return ToolBox(tools)


# --- the other read-only CodeCompass MCP tools -------------------------------

ANALYTICS_TEMPLATES = (
    "document_counts_by_kind",
    "paths_for_kind",
    "graph_relation_counts",
    "snapshot_identity",
)
MAX_TEXT_CHARS = 200
MAX_MANIFEST_CHARS = 8192
_HANDLE = re.compile(r"hac:[0-9a-f]{12}:[A-Za-z0-9_.:/#@-]{1,160}")


def _string(limit):
    def convert(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            return None
        value = " ".join(value.split())[:limit]
        return value or None

    return convert


def _integer(low, high):
    def convert(value):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if type(value) is not int:
            return None
        return max(low, min(high, value))

    return convert


def _boolean(value):
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return value if isinstance(value, bool) else None


def _enum(options):
    def convert(value):
        value = value.strip() if isinstance(value, str) else value
        return value if value in options else None

    return convert


def _manifest(value):
    """An object, or a JSON object written as text by the model; anything else is absent."""
    if isinstance(value, str) and value.strip() and len(value) <= MAX_MANIFEST_CHARS:
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and len(json.dumps(value, default=str)) <= MAX_MANIFEST_CHARS:
        return value
    return None


class HubToolSpec:
    """One model-facing CodeCompass tool: name, MCP name, schema, bounds, usage hint."""

    def __init__(self, name, mcp_name, description, properties, *, required=(), usage, summary=None):
        self.name = name
        self.mcp_name = mcp_name
        self.required = tuple(required)
        # field -> (json schema, converter)
        self.fields = {field: converter for field, (_schema, converter) in properties.items()}
        self.usage = usage
        self.summary = summary or (required[0] if required else None)
        self.definition = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {field: schema for field, (schema, _converter) in properties.items()},
                    "required": list(required),
                    "additionalProperties": False,
                },
            },
        }


def _text_field(description, limit=MAX_TEXT_CHARS):
    return {"type": "string", "description": description}, _string(limit)


HUB_TOOL_SPECS = (
    HubToolSpec(
        "codecompass_architecture_overview",
        "codecompass.architecture_overview",
        "Gestufter Architekturüberblick zu einer Frage über Ananta (Komponenten, Pfade, Beziehungen). "
        "Liefert Handles, die codecompass_architecture_expand vertieft.",
        {
            "query": _text_field("Architekturfrage, z. B. 'Meet Companion Pipeline'."),
            "profile": _text_field("Optionales Profil, Standard 'overview'.", 64),
            "revision": _text_field("Optionale Revision.", 64),
        },
        required=("query",),
        usage="codecompass_architecture_overview braucht 'query' (die Architekturfrage).",
    ),
    HubToolSpec(
        "codecompass_architecture_expand",
        "codecompass.architecture_expand",
        "Vertieft genau einen Handle (hac:…) aus einem vorherigen codecompass_architecture_overview.",
        {
            "handle": _text_field("Handle aus dem Überblick, beginnt mit 'hac:'.", 256),
            "query": _text_field("Optionale Frage für die Vertiefung."),
            "revision": _text_field("Optionale Revision.", 64),
        },
        required=("handle",),
        usage="codecompass_architecture_expand braucht 'handle' aus codecompass_architecture_overview.",
    ),
    HubToolSpec(
        "codecompass_architecture_intelligence",
        "codecompass.architecture_intelligence",
        "Abgeleitete Architekturanalyse: Communities, Smells, Gesundheit. Projektion, keine Quelle; "
        "optional mit snapshot_ref.",
        {
            "snapshot_ref": _text_field("Optionale Snapshot-Referenz.", 256),
            "revision": _text_field("Optionale Revision.", 64),
        },
        usage="codecompass_architecture_intelligence braucht keine Pflichtfelder; snapshot_ref ist optional.",
        summary="snapshot_ref",
    ),
    HubToolSpec(
        "codecompass_layers_heads",
        "codecompass.layers_heads",
        "Listet die inkrementellen CodeCompass-Index-Layer (Profile und Heads). Nur lesend.",
        {"profile_id": _text_field("Optionales Profil, dessen Head gezeigt wird.", 128)},
        usage="codecompass_layers_heads braucht keine Pflichtfelder; profile_id ist optional.",
        summary="profile_id",
    ),
    HubToolSpec(
        "codecompass_layers_plan",
        "codecompass.layers_plan",
        "Trockenlauf eines inkrementellen Index-Updates aus zwei Snapshot-Manifesten (JSON-Objekte). "
        "Nur nutzen, wenn beide Manifeste vorliegen.",
        {
            "old_manifest": ({"type": "object", "description": "Altes Snapshot-Manifest."}, _manifest),
            "new_manifest": ({"type": "object", "description": "Neues Snapshot-Manifest."}, _manifest),
            "profile_id": _text_field("Optionales Profil, Standard 'default'.", 128),
        },
        required=("old_manifest", "new_manifest"),
        usage=(
            "codecompass_layers_plan braucht 'old_manifest' und 'new_manifest' als JSON-Objekte "
            "(Snapshot-Manifeste); ohne Manifeste stattdessen codecompass_layers_heads nutzen."
        ),
        summary="profile_id",
    ),
    HubToolSpec(
        "codecompass_analytics_query",
        "codecompass.analytics_query",
        "Führt eine benannte CodeCompass-Analytics-Vorlage aus (kein freies SQL): Dokumentzahlen je Art, "
        "Pfade einer Art, Graph-Beziehungszahlen, Snapshot-Identität.",
        {
            "template": (
                {"type": "string", "enum": list(ANALYTICS_TEMPLATES), "description": "Name der Vorlage."},
                _enum(ANALYTICS_TEMPLATES),
            ),
            "kind": _text_field("Dokumentart für 'paths_for_kind', z. B. 'python'.", 128),
        },
        required=("template",),
        usage="codecompass_analytics_query braucht 'template', eines von: " + ", ".join(ANALYTICS_TEMPLATES) + ".",
    ),
    HubToolSpec(
        "codecompass_rlm_analyze",
        "codecompass.rlm_analyze",
        "Rekursive, begrenzte CodeCompass-Analyse für verzweigte Fragen; ist sie abgeschaltet, "
        "fällt sie auf die normale Suche zurück.",
        {
            "query": _text_field("Die Analysefrage."),
            "enabled": ({"type": "boolean", "description": "Rekursion anfordern."}, _boolean),
            "max_depth": ({"type": "integer", "minimum": 1, "maximum": 2}, _integer(1, 2)),
            "max_fanout": ({"type": "integer", "minimum": 1, "maximum": 3}, _integer(1, 3)),
        },
        required=("query",),
        usage="codecompass_rlm_analyze braucht 'query' (die Analysefrage).",
    ),
)
HUB_TOOL_ALIASES = {spec.mcp_name: spec.name for spec in HUB_TOOL_SPECS}

_REFUSALS = {
    "meet_tool_denied": "vom Hub für den Companion nicht freigegeben.",
    "meet_media_policy_denied": "vom Hub für dieses Projekt nicht freigegeben.",
    "meet_tool_not_found": "nichts gefunden – Handle oder Ziel ist unbekannt.",
    "meet_tool_timeout": "Zeitüberschreitung im Hub; antworte ohne dieses Ergebnis oder frage enger.",
}


class ToolMemory:
    """What must survive between replies: architecture handles and snapshot refs.

    ``codecompass_architecture_expand`` needs a handle an earlier overview
    returned, often in the previous chat message; the handles are remembered
    here (bounded, newest first) and offered back in the usage hint.
    """

    MAX_HANDLES = 8
    MAX_SNAPSHOTS = 4

    def __init__(self):
        self.handles = []
        self.snapshot_refs = []

    def remember(self, text):
        for handle in _HANDLE.findall(str(text or "")):
            if handle in self.handles:
                self.handles.remove(handle)
            self.handles.insert(0, handle)
        del self.handles[self.MAX_HANDLES:]
        for ref in re.findall(r'"snapshot_ref":"([^"]{1,256})"', str(text or "")):
            if ref in self.snapshot_refs:
                self.snapshot_refs.remove(ref)
            self.snapshot_refs.insert(0, ref)
        del self.snapshot_refs[self.MAX_SNAPSHOTS:]


class HubTool:
    """One read-only CodeCompass MCP tool executed by the Hub (``assist.call_tool``)."""

    def __init__(self, spec, hub, memory=None):
        """``hub(mcp_name, arguments)`` -> ``{"text", "truncated", "sources"}`` or raises."""
        self.spec = spec
        self.name = spec.name
        self.definition = spec.definition
        self._hub = hub
        self.memory = memory if memory is not None else ToolMemory()
        self.calls = []
        self.sources = []

    def reset(self):
        """Per-reply trace only; the handle memory deliberately survives."""
        self.calls = []
        self.sources = []

    def arguments(self, raw):
        """Model arguments reduced to the spec's fields; unusable values are dropped."""
        arguments = {}
        for field, value in parse_arguments(raw).items():
            converter = self.spec.fields.get(field)
            converted = converter(value) if converter is not None else None
            if converted is not None:
                arguments[field] = converted
        return arguments

    def usage(self):
        hint = self.name + ": " + self.spec.usage
        if self.spec.mcp_name == "codecompass.architecture_expand":
            hint += (
                " Bekannte Handles: " + ", ".join(self.memory.handles[:4]) + "."
                if self.memory.handles
                else " Rufe zuerst codecompass_architecture_overview auf."
            )
        if self.spec.mcp_name == "codecompass.architecture_intelligence" and self.memory.snapshot_refs:
            hint += " Bekannte snapshot_ref: " + ", ".join(self.memory.snapshot_refs[:2]) + "."
        return hint

    def run(self, arguments):
        """Return one bounded text block; never raises, never leaks a reason."""
        arguments = self.arguments(arguments)
        missing = [field for field in self.spec.required if field not in arguments]
        if missing:
            return self.usage()
        if len(self.calls) >= MAX_CALLS_PER_REPLY:
            return BUDGET_EXHAUSTED
        summary = str(arguments.get(self.spec.summary) or "")[:MAX_QUERY_CHARS] if self.spec.summary else ""
        call = {"tool": self.name, "query": summary, "snippets": 0, "failed": False}
        self.calls.append(call)
        try:
            result = self._hub(self.spec.mcp_name, arguments)
        except Exception as error:  # noqa: BLE001 -- a refused or failed tool is a tool result
            call["failed"] = True
            code = getattr(error, "code", "")
            call["code"] = code if isinstance(code, str) else ""
            if code == "meet_tool_arguments_invalid":
                return self.usage() + " (Der Hub hat die Argumente abgelehnt.)"
            return self.name + ": " + _REFUSALS.get(code, "das Werkzeug ist gerade nicht erreichbar.")
        text = str((result or {}).get("text") or "").strip()
        sources = [item for item in ((result or {}).get("sources") or []) if isinstance(item, dict)]
        call["snippets"] = len(sources)
        self.sources.extend(sources[:MAX_LIMIT])
        self.memory.remember(text)
        if not text or text in ("{}", "[]", "null"):
            return self.name + ": kein Ergebnis."
        head = self.name + ": "
        if self.spec.mcp_name in ("codecompass.architecture_overview", "codecompass.architecture_expand"):
            handles = _HANDLE.findall(text)[:4]
            if handles:
                # First, so the bounded result block never cuts them off.
                head += "[Handles für codecompass_architecture_expand: " + ", ".join(handles) + "] "
        if (result or {}).get("truncated"):
            head += "[gekürzt] "
        return head + text
