"""Config graph classifiers: path-character and rule classification functions.

Extracted from config_graph_builder_service.py (VACGE-001/002, FSR-M09).
"""
from __future__ import annotations

from agent.services.config_graph_models import (
    PATH_CHARACTER_TEST,
    PATH_CHARACTER_ANALYSIS,
    PATH_CHARACTER_OPS,
    PATH_CHARACTER_MAINTENANCE,
    PATH_CHARACTER_CREATIVE,
    PATH_CHARACTER_EXPLAIN,
    PATH_CHARACTER_UNKNOWN,
    _ROLE_CHARACTER_RULES,
    PATH_CHARACTER_LABELS,
)


def _classify_profile_character(role: str, profile_id: str) -> str:
    """Infer path character from role name and profile id."""
    text = (role + " " + profile_id).lower()
    for keywords, character in _ROLE_CHARACTER_RULES:
        if any(kw in text for kw in keywords):
            return character
    return PATH_CHARACTER_UNKNOWN


def _classify_rule_character(blocked: list[str], allowed: list[str]) -> str:
    """Classify a path rule by its AI-mode constraints."""
    if "full_llm" in blocked:
        return "kein_vollstaendiges_llm"
    if blocked:
        return "eingeschraenkt"
    if allowed:
        return "selektiv_erlaubt"
    return "offen"


# ── Behavioral dimensions ─────────────────────────────────────────────────────
#
# Structured annotations that explain WHAT an agent profile actually DOES at
# runtime — beyond the raw policy strings.  Shown in the config graph detail
# view so operators can understand the internal differences between profiles.
#

_EXECUTE_CONTRACT: dict[str, dict] = {
    "none": {
        "label": "Nur Lesen",
        "description": (
            "Keine Code-Änderungen möglich. Ausschließlich lesende Operationen. "
            "Ausgabe als strukturierter Befund oder Erklärung."
        ),
        "gate": "blocked",
        "can_write_files": False,
        "can_run_commands": False,
        "mechanism": "read_only",
    },
    "plan_only": {
        "label": "Vorschlag + Freigabe",
        "description": (
            "Darf Aktionen nur als Plan vorschlagen, nie direkt ausführen. "
            "Jeder Vorschlag muss Risiko-Level und Rollback-Plan enthalten. "
            "Ausführung erfordert explizite Freigabe durch den Operator."
        ),
        "gate": "explicit_approval_required",
        "can_write_files": False,
        "can_run_commands": False,
        "mechanism": "propose_only",
    },
    "via_hub_task_worker": {
        "label": "Hub-Task-Worker",
        "description": (
            "Änderungen werden über den Hub-Task-Worker ausgeführt. "
            "Der Hub validiert automatisch und serialisiert parallele Änderungen. "
            "Kein direkter Dateizugriff durch den Agenten selbst."
        ),
        "gate": "hub_validated",
        "can_write_files": True,
        "can_run_commands": True,
        "mechanism": "hub_worker",
    },
}

_CONTEXT_AUTHORITY: dict[str, dict] = {
    "diagnose": {
        "label": "Diagnose-Kontext",
        "description": (
            "Logs, Config-Dateien und Kommando-Output sind die primäre Wahrheitsquelle. "
            "CodeCompass wird nur für Projekt-Dateien verwendet, "
            "nicht als Host-Wahrheit für laufende Systeme."
        ),
        "primary_sources": ["logs", "config_files", "command_output"],
        "codecompass": "secondary",
    },
    "implement": {
        "label": "Implementierungs-Kontext",
        "description": (
            "Source-Code und Test-Output sind autoritativ. "
            "CodeCompass routet primär zu Kandidaten-Dateien für die Implementierung."
        ),
        "primary_sources": ["source_code", "test_output"],
        "codecompass": "primary",
    },
    "analyse": {
        "label": "Analyse-Kontext",
        "description": (
            "Alle Quellen werden lesend ausgewertet: Code, Logs, Git-History. "
            "Ausgabe als strukturierter, evidenzbasierter Befund."
        ),
        "primary_sources": ["source_code", "logs", "git_history", "config_files"],
        "codecompass": "primary",
    },
    "explain_navigate": {
        "label": "Erklär-/Navigations-Kontext",
        "description": (
            "Navigation und Erklärung im bestehenden Code. "
            "Keine Modifikationsabsicht — reiner Lesezugriff."
        ),
        "primary_sources": ["source_code", "codecompass"],
        "codecompass": "primary",
    },
    "plan_only": {
        "label": "Planungs-Kontext",
        "description": "Nur Planungsaktivitäten ohne Ausführung.",
        "primary_sources": ["source_code"],
        "codecompass": "secondary",
    },
}

_SCOPE_MUST_NOT: dict[str, list[str]] = {
    PATH_CHARACTER_TEST: [
        "Produktions-Logik ändern ohne explizite Freigabe",
        "Fehlschlagende Tests löschen statt reparieren",
        "Test-Fixtures ohne Begründung überschreiben",
    ],
    PATH_CHARACTER_ANALYSIS: [
        "Code-Änderungen vorschlagen oder ausführen",
        "Annahmen als Fakten ausgeben — nur evidenzbasierte Befunde",
        "Externe Quellen ohne Verifikation zitieren",
    ],
    PATH_CHARACTER_OPS: [
        "Destruktive Aktionen ohne explizite Freigabe ausführen",
        "Dry-run-Schritt überspringen",
        "Host-Logs/Config mit CodeCompass-Annahmen überschreiben",
        "Risiko-Level im Plan weglassen",
    ],
    PATH_CHARACTER_MAINTENANCE: [
        "Architektur-Umbau statt minimalem Patch",
        "Nicht-autorisierte Dateien ändern",
        "Verhalten ohne Test-Abdeckung ändern",
    ],
    PATH_CHARACTER_CREATIVE: [
        "Bestehende Verträge brechen ohne Migration",
        "Abhängigkeiten ohne Zustimmung hinzufügen",
    ],
    PATH_CHARACTER_EXPLAIN: [
        "Code-Änderungen vornehmen",
        "Interne Implementierungsdetails ohne Kontext preisgeben",
    ],
    PATH_CHARACTER_UNKNOWN: [],
}


def _build_behavior_dimensions(pdata: dict) -> dict:
    """Derive structured behavioral annotations for an agent profile node."""
    policy = str(pdata.get("code_change_policy") or "none")
    hint = str(pdata.get("context_policy_hint") or "implement")
    role = str(pdata.get("primary_role") or "")
    profile_id = str(pdata.get("profile_id") or "")

    character = _classify_profile_character(role, profile_id)

    execute = dict(_EXECUTE_CONTRACT.get(policy, _EXECUTE_CONTRACT["none"]))
    execute["policy"] = policy

    context = dict(_CONTEXT_AUTHORITY.get(hint, _CONTEXT_AUTHORITY["implement"]))
    context["hint"] = hint

    must_not = _SCOPE_MUST_NOT.get(character, [])

    return {
        "execute_contract": execute,
        "context_authority": context,
        "must_not": must_not,
        "scope": character,
        "scope_label": PATH_CHARACTER_LABELS.get(character, "Allgemein"),
    }
