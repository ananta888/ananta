"""``final_answer``: how a single-shot analysis task hands over its answer.

The autopilot's flow is one proposal, one execution; the proposal knows commands and tool
calls only. An analysis step (e.g. a long-context split step) has nothing to execute -- it
answers. ``final_answer`` carries that answer without any side effect (class ``read``), so
it passes the ``safe`` security level, which blocks writes. The Hub reads the answer from
the stored proposal (``long_context_step_result``); the execution output repeats it.
"""

from agent.tools_registry import registry


@registry.register(
    name="final_answer",
    description="Gibt das Ergebnis der Aufgabe ab (Text/Markdown). Keine Seiteneffekte.",
    parameters={
        "type": "object",
        "properties": {"answer": {"type": "string", "description": "Das vollständige Ergebnis"}},
        "required": ["answer"],
    },
)
def final_answer_tool(answer: str = "", **_ignored):
    text = str(answer or "").strip()
    if not text:
        return {"error": "Parameter 'answer' fehlt oder ist leer."}
    return {"answer": text}
