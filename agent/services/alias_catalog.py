"""The names this system ships with.

Everything here is data, not logic: which technical identifier is called what,
and which other names must find it.  The mechanism lives in
:mod:`agent.services.alias_registry`; this module only populates it.

The display names are written for someone assembling a small team who has
never read the code.  The technical name is kept as an alias throughout, so
anyone who already knows it keeps finding what they know.
"""

from __future__ import annotations

from functools import lru_cache

from agent.services.alias_registry import (
    ALIAS_NAMESPACE_AGENT_ROLE,
    ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
    ALIAS_NAMESPACE_WORKFLOW_RUNTIME,
    AliasRegistry,
    StaticAliasSource,
    alias_entries_from_mapping,
)

VISUAL_PROCESS_PRESET_ALIASES: dict[str, dict[str, object]] = {
    "preset-code-review": {
        "display_name": "Code prüfen lassen",
        "aliases": ["Code Review", "Review-Team", "Code Review Pipeline"],
        "description": "Ein Team liest bestehenden Code, baut Änderungen ein, testet und prüft gegen.",
    },
    "preset-tdd-loop": {
        "display_name": "Erst Test, dann Code",
        "aliases": ["TDD", "TDD Loop", "Testgetrieben"],
        "description": "Ein Agent schreibt einen fehlschlagenden Test, ein zweiter baut, bis er grün ist.",
    },
    "preset-research-report": {
        "display_name": "Recherchieren und berichten",
        "aliases": ["Recherche", "Research and Report", "Report"],
        "description": "Informationen sammeln, zusammenfassen und einen Bericht schreiben.",
    },
    "preset-deploy-pipeline": {
        "display_name": "Testen und ausliefern",
        "aliases": ["Deploy", "Deploy Pipeline", "Auslieferung"],
        "description": "Tests laufen lassen, bauen und erst nach bestandenem Gate ausliefern.",
    },
    "preset-builder-critic-gauntlet": {
        "display_name": "Bauen & Prüfen im Wechsel",
        "aliases": ["Builder-Critic", "Gauntlet", "Builder/Critic Gauntlet"],
        "description": "Eine Leitung verteilt, einer baut, einer prüft und schickt begründet zurück.",
    },
    "preset-rag-pipeline": {
        "display_name": "Fragen an eigene Dokumente",
        "aliases": ["RAG", "RAG-Pipeline", "Dokumentensuche"],
        "description": "Frage schärfen, passende Stellen abrufen, neu sortieren und beantworten.",
    },
    "preset-knowledge-index": {
        "display_name": "Wissen durchsuchbar machen",
        "aliases": ["Wissensindex", "Knowledge Index", "Indexierung"],
        "description": "Dokumente zerlegen, einbetten, komprimieren und ablegen.",
    },
    "preset-self-improving-agent": {
        "display_name": "Sich selbst verbessern",
        "aliases": ["Self-Improving Agent", "Verbesserungsloop", "Autonomer Loop"],
        "description": "Planen, umsetzen, testen und aus dem Ergebnis die nächste Runde ableiten.",
    },
    "preset-self-improving-planner": {
        "display_name": "Nur planen und nachschärfen",
        "aliases": ["Self-Improving Planner", "Planer"],
        "description": "Wie oben, aber ohne Umsetzung: nur planen und den Prompt verbessern.",
    },
    "preset-evolution-pipeline": {
        "display_name": "Varianten züchten",
        "aliases": ["Evolution", "Evolution Pipeline", "EvolutionService"],
        "description": "Kontext analysieren, Varianten erzeugen, bewerten und die beste behalten.",
    },
    "preset-codecompass-search": {
        "display_name": "Im Code etwas finden",
        "aliases": ["CodeCompass", "CodeCompass Suche", "Codesuche"],
        "description": "Index aktualisieren, semantisch und per Volltext suchen, Treffer zusammenführen.",
    },
    "preset-workspace-snapshot-diff": {
        "display_name": "Änderungen nachvollziehen",
        "aliases": ["Workspace Diff", "Snapshot", "Diff Pipeline"],
        "description": "Zustand vorher festhalten, Änderung anwenden, Unterschied berechnen und ablegen.",
    },
}

WORKFLOW_RUNTIME_ALIASES: dict[str, dict[str, object]] = {
    "ananta-native": {
        "display_name": "Ananta (eingebaut)",
        "aliases": ["Native", "local"],
        "description": "Die mitgelieferte Ausführung ohne zusätzliche Dienste.",
    },
    "langgraph": {
        "display_name": "LangGraph",
        "aliases": ["Lang Graph"],
        "description": "Ausführung über einen LangGraph-Worker.",
    },
    "temporal": {
        "display_name": "Temporal",
        "aliases": [],
        "description": "Ausführung über einen Temporal-Cluster.",
    },
}

AGENT_ROLE_ALIASES: dict[str, dict[str, object]] = {
    "architect": {"display_name": "Architektur", "aliases": ["Architect", "Architekt"]},
    "developer": {"display_name": "Entwicklung", "aliases": ["Developer", "Entwickler"]},
    "frontend": {"display_name": "Oberfläche", "aliases": ["Frontend", "UI"]},
    "backend": {"display_name": "Server", "aliases": ["Backend"]},
    "devops": {"display_name": "Betrieb", "aliases": ["DevOps", "Ops"]},
    "tester": {"display_name": "Test", "aliases": ["Tester", "QA"]},
    "security": {"display_name": "Sicherheit", "aliases": ["Security"]},
    "reviewer": {"display_name": "Gegenlesen", "aliases": ["Reviewer", "Review"]},
    "product_owner": {"display_name": "Produkt", "aliases": ["Product Owner", "PO"]},
    "scrum_master": {"display_name": "Moderation", "aliases": ["Scrum Master", "SM"]},
}


@lru_cache(maxsize=1)
def default_alias_registry() -> AliasRegistry:
    """The registry the application renders and resolves names through."""

    return AliasRegistry(
        [
            StaticAliasSource(
                (
                    *alias_entries_from_mapping(
                        ALIAS_NAMESPACE_VISUAL_PROCESS_PRESET,
                        VISUAL_PROCESS_PRESET_ALIASES,
                    ),
                    *alias_entries_from_mapping(
                        ALIAS_NAMESPACE_WORKFLOW_RUNTIME,
                        WORKFLOW_RUNTIME_ALIASES,
                    ),
                    *alias_entries_from_mapping(
                        ALIAS_NAMESPACE_AGENT_ROLE,
                        AGENT_ROLE_ALIASES,
                    ),
                )
            )
        ]
    )


__all__ = [
    "AGENT_ROLE_ALIASES",
    "VISUAL_PROCESS_PRESET_ALIASES",
    "WORKFLOW_RUNTIME_ALIASES",
    "default_alias_registry",
]
