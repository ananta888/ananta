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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "outcome": self.outcome,
            "tasks": list(self.tasks),
            "starter_context": self.starter_context,
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
