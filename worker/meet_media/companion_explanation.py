"""Self-explanation of the companion's last answer from recorded evidence.

An ``AnswerTrace`` records three strictly separated kinds of statements:

* ``observed``   – runtime steps that actually happened in this worker
  (route decision, retrieval call, model call, TTS, avatar clips).
* ``repository`` – sources CodeCompass returned (path, symbol, revision).
* ``generated``  – the reply text itself, which the LLM formulated.

``explain`` composes the answer to "Wie hast du diese Antwort erzeugt?"
deterministically from the trace. Nothing is inferred: when no source was
retrieved, the explanation says so instead of naming files.
"""

from dataclasses import dataclass, field

MAX_STEPS = 32
MAX_SOURCES = 8
_SHORT_SHA = 12


@dataclass(frozen=True)
class RepositorySource:
    path: str
    symbol: str = ""
    revision: str = ""
    score: float | None = None
    line: int | None = None

    def location(self):
        """``datei:zeile`` when the line is known, else the bare path."""
        if self.path and self.line:
            return f"{self.path}:{self.line}"
        return self.path

    def label(self):
        text = self.location() or "(ohne Pfad)"
        if self.symbol:
            text += f" · {self.symbol}"
        if self.revision:
            text += f" @ {self.revision[:_SHORT_SHA]}"
        return text


@dataclass
class AnswerTrace:
    question: str
    route: str
    codecompass_used: bool = False
    observed: list = field(default_factory=list)
    repository: list = field(default_factory=list)
    generated_text: str = ""
    generated_by: str = ""

    def observe(self, step):
        step = " ".join(str(step or "").split())[:200]
        if step and len(self.observed) < MAX_STEPS:
            self.observed.append(step)

    def add_sources(self, snippets):
        for item in snippets:
            if len(self.repository) >= MAX_SOURCES:
                break
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            revision = str(item.get("revision") or "").strip()
            score = item.get("score")
            if not path and not symbol:
                continue
            line = item.get("line")
            source = RepositorySource(
                path,
                symbol,
                revision,
                float(score) if isinstance(score, (int, float)) else None,
                line if type(line) is int and line > 0 else None,
            )
            if source not in self.repository:
                self.repository.append(source)

    def source_labels(self):
        return [source.label() for source in self.repository]


def explain(trace, *, max_chars=450):
    """German self-explanation with observed / repository / generated parts."""
    if trace is None:
        text = (
            "Ich habe in diesem Gespräch noch keine Antwort erzeugt, "
            "deshalb kann ich noch keinen Ablauf erklären."
        )
        return text[:max_chars]
    observed = ", ".join(trace.observed[:6]) or "keine Schritte aufgezeichnet"
    parts = [f"Beobachtet: {observed}."]
    if trace.repository:
        parts.append("Aus dem Repository: " + "; ".join(trace.source_labels()[:4]) + ".")
    elif trace.codecompass_used:
        parts.append("Aus dem Repository: CodeCompass hat keine passende Quelle geliefert, daher nenne ich keine Datei.")
    else:
        parts.append("Aus dem Repository: nichts, diese Frage lief ohne CodeCompass.")
    who = trace.generated_by or "dem konfigurierten Sprachmodell"
    parts.append(f"Formuliert wurde der Antworttext von {who}; Ablauf und Quellen sind nicht generiert.")
    text = " ".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
