"""Bounded final-synthesis prompt assembly for RAG Snake answers."""

from __future__ import annotations


def build_synthesis_prompt(
    *,
    question: str,
    architecture_context: str,
    codecompass_evidence: list[str],
    evidence: str,
    research_hint: str,
    retry: bool,
) -> str:
    architecture_budget = 1800 if retry else 3000
    tool_budget = 2200 if retry else 4000
    evidence_budget = 1800 if retry else 3500
    hint_budget = 800 if retry else 1200
    prompt = (
        "Du bist das finale Coding-Synthesemodell. Beantworte die Nutzerfrage verbindlich, "
        "konkret und ausschliesslich aus der folgenden Recherche-Evidenz. Fuehre keine Tool-Aufrufe aus.\n\n"
        f"Nutzerfrage: {question[:1000]}\n\n"
    )
    if architecture_context.strip():
        prompt += f"Architektur-Evidenz:\n{architecture_context[:architecture_budget]}\n\n"
    if codecompass_evidence:
        joined_evidence = "\n\n".join(codecompass_evidence)[-tool_budget:]
        prompt += f"Weitere CodeCompass-Tool-Evidenz:\n{joined_evidence}\n\n"
    prompt += evidence[:evidence_budget]
    if research_hint.strip():
        prompt += f"\n\nVorlaeufiger LFM-Recherchehinweis:\n{research_hint[:hint_budget].strip()}"
    return prompt + (
        "\n\nAntworte auf Deutsch mit hoechstens 700 Woertern. "
        "Nenne nur Beziehungen, die in der Evidenz belegt sind."
    )
