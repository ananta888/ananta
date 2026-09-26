"""Deterministic question router for the companion avatar.

Decides *where* an answer is grounded before any model is called:

* ``self_explanation``           – "Wie hast du diese Antwort erzeugt?" is
  answered from the recorded runtime trace, never generated.
* ``ananta_code_architecture``   – Ananta/repository/self questions go through
  CodeCompass retrieval.
* ``meet_runtime_current_dialog``– questions about the running meeting use the
  runtime context (plus CodeCompass when the question is also about code).
* ``general_question``           – smalltalk and general questions use the
  configured LLM only; they are never routed through CodeCompass.

Every route that carries a repository signal is also a *knowledge* question
(``RouteDecision.knowledge``): the dialog then runs ``codecompass_search``
itself before the model is asked, with ``RouteDecision.query`` as the search
term, instead of leaving the lookup to the model's discretion. A knowledge
phrase ("was ist X", "wie funktioniert X", "was weißt du über X") counts as a
repository signal only when X looks like an identifier (``rag-helper``,
``rag_helper``, ``RagHelperIndexService``, ``docs/rag-helper.md``), so
"Was ist die Hauptstadt von Frankreich?" stays a general question.

Keyword matching is intentionally simple and auditable; it is a policy
decision, so no LLM is involved and the result is reproducible.
"""

import re
from dataclasses import dataclass

SELF_EXPLANATION = "self_explanation"
ANANTA_CODE_ARCHITECTURE = "ananta_code_architecture"
MEET_RUNTIME_CURRENT_DIALOG = "meet_runtime_current_dialog"
GENERAL_QUESTION = "general_question"
ROUTES = frozenset({SELF_EXPLANATION, ANANTA_CODE_ARCHITECTURE, MEET_RUNTIME_CURRENT_DIALOG, GENERAL_QUESTION})

_SELF_EXPLANATION = re.compile(
    r"(wie hast du (diese|die|deine) antwort|wie bist du (auf|zu) (diese|die) antwort|"
    r"woher (hast|weißt|weisst) du das|how did you (come up with|generate|produce) (that|this|the) answer|"
    r"erkl[aä]r(e)? (mir )?(deine|die) letzte antwort|quellen (für|fuer) (deine|die) (letzte )?antwort)",
    re.IGNORECASE,
)
_SELF_PIPELINE = re.compile(
    r"(deine (sprachausgabe|stimme|avatar|mund|animation|pipeline|funktionsweise|architektur)|"
    r"wie funktionierst du|wie arbeitest du|your (speech|voice|avatar|mouth|animation|pipeline)|"
    r"how do you (work|speak|talk|render)|lip.?sync|lippensync)",
    re.IGNORECASE,
)
_CODE = re.compile(
    r"(\bananta\b|codecompass|\brepositor|\brepo\b|\bhub\b|\bworker\b|symbolgraph|architektur|architecture|"
    r"\bdatei\b|\bfile\b|\bmodul|\bklasse\b|\bclass\b|\bfunktion(en)?\b|\bfunction\b|\bendpoint|\broute\b|"
    r"\bcommit\b|\bservice\b|\bcode\b|\brag\b|\bpipeline|\bschema\b|\bskript|\bscript\b|"
    r"welche datei|which file|wo (steht|ist) (der|das|die) code|implementiert)",
    re.IGNORECASE,
)
_MEET = re.compile(
    r"(\bmeet(ing)?\b|\braum\b|\broom\b|teilnehmer|participant|\bchat\b|webrtc|\bmikro|\baudio\b|\bvideo\b|"
    r"gerade|jetzt|aktuell|dieses gespr[aä]ch|this (call|meeting|conversation))",
    re.IGNORECASE,
)

_KNOWLEDGE = re.compile(
    r"(was (ist|sind|macht|machen|bedeutet)\b|wie (funktioniert|funktionieren|arbeitet|l[aä]uft)\b|"
    r"was wei(ß|ss|s)t du (über|ueber|von)|erkl[aä]r(e|st)? (mir )?|wof[uü]r (ist|sind|dient|gibt)|"
    r"wozu (ist|dient|gibt)|welche (datei|klasse|funktion|stelle)|wo (liegt|steht|ist|wird|finde)|"
    r"what (is|are|does)|how (does|do) .{1,60} work|tell me about)",
    re.IGNORECASE,
)
# Case-sensitive on purpose: identifiers are lower-case kebab/snake names,
# CamelCase symbols, paths or file names; "Covid-19" or "E-Mail" are not.
_IDENTIFIER = re.compile(
    r"(\b[a-z][a-z0-9]+(?:[-_][a-z0-9]+)+\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b|"
    r"\b[\w-]+/[\w./-]+|\b[\w-]+\.(?:py|ts|js|md|json|ya?ml|sh|toml)\b)"
)
# Word boundaries: "wo" must not be cut out of "Antworten".
_QUERY_PHRASES = re.compile(
    r"\b(was wei(ß|ss|s)t du (über|ueber|von)|wie (funktioniert|funktionieren|arbeitet|l[aä]uft)|"
    r"was (ist|sind|macht|machen|bedeutet)|erkl[aä]r(e|st)?|wof[uü]r|wozu|welche|wo|"
    r"what (is|are|does)|how (does|do)|tell me about)\b",
    re.IGNORECASE,
)
# Question words, pronouns and auxiliary verbs carry no search signal: the
# forced lookup used to search "alles für funktionen dafür aufrufen kannst".
_QUERY_FILLER = frozenset(
    "der die das den dem des ein eine einen einem du dir mir mich ich es er sie ist sind "
    "dein deine deiner deinen macht machen "
    "genau eigentlich denn noch mal bitte so und oder über ueber von zu im in am the a an is "
    "are it work works "
    "was wie wer wen wem welcher welches welchen warum weshalb wann ob dass "
    "alle alles allem allen für fuer dafür dafuer damit davon dazu darüber darueber hier da dann also "
    "kann kannst können koennen könntest koenntest soll sollst wird werden wurde wurden hat hast haben "
    "habe gibt geben funktioniert funktionieren "
    "zeig zeige sag sage nenn nenne beschreib beschreibe "
    "dies diese dieser dieses jetzt nun auch nur schon sehr mehr uns euch ihr wir man "
    "what how which why can could do does did you your me my this that these those".split()
)
MAX_QUERY_CHARS = 200


@dataclass(frozen=True)
class RouteDecision:
    route: str
    codecompass: bool
    reason: str
    # Repository knowledge question: ground it before the model answers.
    knowledge: bool = False
    # Search term for the forced lookup (identifiers first, else the stripped question).
    query: str = ""


def search_query(text):
    """Bounded CodeCompass search term for a knowledge question.

    Identifiers are the strongest signal ("rag-helper"); without one, the
    question phrase and filler words are dropped. Never empty for a
    non-empty question.
    """
    text = " ".join(str(text or "").split())[:1000]
    identifiers = list(dict.fromkeys(match.group(0) for match in _IDENTIFIER.finditer(text)))
    if identifiers:
        return " ".join(identifiers)[:MAX_QUERY_CHARS]
    words = [
        word
        for word in re.findall(r"[\w-]+", _QUERY_PHRASES.sub(" ", text))
        if word.lower() not in _QUERY_FILLER
    ]
    return (" ".join(words) or text)[:MAX_QUERY_CHARS]


def classify(text, *, codecompass_enabled=True):
    """Return the grounded route for ``text``; deterministic and bounded."""
    text = " ".join(str(text or "").split())[:1000]
    if not text:
        return RouteDecision(GENERAL_QUESTION, False, "empty")
    if _SELF_EXPLANATION.search(text):
        return RouteDecision(SELF_EXPLANATION, False, "self_explanation_phrase")
    code_phrase = bool(_CODE.search(text)) or bool(_KNOWLEDGE.search(text) and _IDENTIFIER.search(text))
    self_phrase = bool(_SELF_PIPELINE.search(text))
    is_meet = bool(_MEET.search(text))
    if is_meet and not code_phrase and not self_phrase:
        return RouteDecision(MEET_RUNTIME_CURRENT_DIALOG, False, "meet_runtime_phrase")
    if code_phrase or self_phrase:
        route = MEET_RUNTIME_CURRENT_DIALOG if is_meet and not self_phrase else ANANTA_CODE_ARCHITECTURE
        return RouteDecision(
            route, bool(codecompass_enabled), "code_or_self_phrase", knowledge=True, query=search_query(text)
        )
    return RouteDecision(GENERAL_QUESTION, False, "no_repository_signal")
