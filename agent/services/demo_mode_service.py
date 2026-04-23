from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DemoExample:
    id: str
    title: str
    goal: str
    outcome: str
    tasks: tuple[str, ...]
    starter_context: str
    path_summary: str = ""
    artifacts: tuple[str, ...] = ()
    governance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "outcome": self.outcome,
            "tasks": list(self.tasks),
            "starter_context": self.starter_context,
            "path_summary": self.path_summary,
            "artifacts": list(self.artifacts),
            "governance": list(self.governance),
        }


class DemoModeService:
    """Builds read-only demo previews without touching productive task state."""

    def preview(self) -> dict[str, Any]:
        examples = (
            DemoExample(
                id="repo-analysis",
                title="Repository verstehen",
                goal="Analysiere ein neues Repository und fasse Architektur, Risiken und naechste Schritte zusammen.",
                outcome="Ein klarer Einstieg mit Hotspots, offenen Fragen und konkretem Arbeitsplan.",
                tasks=("Projektstruktur lesen", "Architekturgrenzen pruefen", "Review-Plan erstellen"),
                starter_context="Fokus: Einstieg fuer neue Maintainer, Risiken benennen, keine Code-Aenderungen.",
            ),
            DemoExample(
                id="bugfix-plan",
                title="Bugfix vorbereiten",
                goal=(
                    "Untersuche einen Fehlerbericht, grenze die Ursache ein "
                    "und plane eine kleine, testbare Korrektur."
                ),
                outcome="Ein nachvollziehbarer Fix-Plan mit passenden Tests statt blindem Code-Aendern.",
                tasks=("Fehler reproduzieren", "Betroffene Pfade finden", "Fix und Regressionstest vorschlagen"),
                starter_context="Fokus: kleine, testbare Korrektur planen und Regressionen vermeiden.",
            ),
            DemoExample(
                id="compose-diagnosis",
                title="Lokalen Start reparieren",
                goal="Pruefe Docker-/Compose-Probleme und leite eine robuste lokale Startsequenz ab.",
                outcome="Konkrete Startbefehle, bekannte Stolperstellen und sichere naechste Diagnose.",
                tasks=("Compose-Profile pruefen", "Ports und Health-Checks auswerten", "Startpfad dokumentieren"),
                starter_context="Fokus: lokaler Start, Compose-Profile, Health-Checks und klare naechste Diagnose.",
            ),
            DemoExample(
                id="change-review",
                title="Change Review",
                goal="Pruefe eine Aenderung auf Risiken, fehlende Tests und moegliche Regressionen.",
                outcome="Priorisierte Findings, Testbedarf und Governance-Hinweise.",
                tasks=("Diff-Hotspots pruefen", "Risiken priorisieren", "Test- und Review-Plan erstellen"),
                starter_context="Fokus: Review statt Implementierung. Keine automatischen Aenderungen ohne Freigabe.",
            ),
            DemoExample(
                id="guided-first-run",
                title="Gefuehrter erster Lauf",
                goal="Erstelle ein erstes kontrolliertes Goal mit Kontext, Ausfuehrungstiefe und Sicherheitsniveau.",
                outcome="Ein parametrisiertes Goal mit sichtbaren Safety- und Review-Entscheidungen.",
                tasks=("Ziel klaeren", "Kontext sammeln", "Sicherheitsniveau pruefen"),
                starter_context="Fokus: Erstnutzerfuehrung, sichtbare Governance und klarer naechster Schritt.",
            ),
            DemoExample(
                id="new-software-project",
                title="Neues Projekt anlegen",
                goal=(
                    "Lege ein neues Softwareprojekt aus einer Idee an und erstelle Scope, "
                    "Architekturvorschlag, initiales Backlog und sichere naechste Schritte."
                ),
                outcome="Ein reviewbarer Projekt-Blueprint mit kleinen Initial-Tasks.",
                tasks=("Projektidee klaeren", "Blueprint erstellen", "Initial-Tasks priorisieren"),
                starter_context="Fokus: neuer Projektstart, sichere Defaults, keine Vollautomatik ohne Review.",
            ),
            DemoExample(
                id="project-evolution",
                title="Projekt weiterentwickeln",
                goal=(
                    "Plane eine kontrollierte Weiterentwicklung eines bestehenden Projekts "
                    "mit betroffenen Bereichen, Risiken, Tests und Review-Schritten."
                ),
                outcome="Ein kleiner, verifizierbarer Aenderungsplan fuer ein bestehendes Repository.",
                tasks=("Ist-Kontext schaerfen", "Aenderungsschritte zerlegen", "Tests und Risiken pruefen"),
                starter_context="Fokus: aktive Weiterentwicklung statt reiner Analyse, kleine pruefbare Schritte.",
            ),
            DemoExample(
                id="research-evolution",
                title="Research -> Proposal -> Review",
                goal=(
                    "Erweitere ein bestehendes Projekt um ein kleines Feature; recherchiere zuerst "
                    "relevante Quellen und erstelle danach reviewbare Evolver-Proposals."
                ),
                outcome="Research-Bericht, reviewbares Proposal und ein sichtbares Review-Gate fuer die naechsten Schritte.",
                tasks=("Scope schaerfen", "Research-Artefakt erstellen", "Proposal und Review-Gate vorbereiten"),
                starter_context=(
                    "Fokus: erst Recherche und Kontextbericht mit DeerFlow, dann reviewbare Evolver-Vorschlaege. "
                    "Keine impliziten Apply-Schritte ohne Review."
                ),
                path_summary="DeerFlow liefert Kontext und Quellen; Evolver erzeugt daraus Proposals; der Hub haelt Review, Policy und Folge-Tasks sichtbar.",
                artifacts=("Research Summary", "Source List", "Evolver Proposal", "Review Gate"),
                governance=(
                    "Hub bleibt Besitzer von Queue, Review und Policy-Entscheidungen.",
                    "Apply bleibt standardmaessig deaktiviert.",
                    "Review ist vor spaeterer Validation oder Apply-Freigabe sichtbar.",
                ),
            ),
        )
        return {
            "mode": "preview",
            "isolated": True,
            "description": "Demo-Beispiele sind read-only und werden nicht in echte Goals oder Tasks geschrieben.",
            "examples": [example.to_dict() for example in examples],
        }


_demo_mode_service = DemoModeService()


def get_demo_mode_service() -> DemoModeService:
    return _demo_mode_service
