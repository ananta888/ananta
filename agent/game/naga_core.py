from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TutorialStep:
    id: str
    topic: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class NagaCoreGuide:
    def policy_authority(self) -> bool:
        return False

    def tutorial_steps(self) -> tuple[TutorialStep, ...]:
        return (
            TutorialStep(
                id="nagacore-hub",
                topic="AegisHub",
                message="AegisHub steuert Delegation und Approval zentral; Worker orchestrieren keine Worker.",
            ),
            TutorialStep(
                id="nagacore-context",
                topic="ContextAegis",
                message="Kontextfreigaben sind Default-Deny und werden nur explizit freigegeben.",
            ),
            TutorialStep(
                id="nagacore-artifact",
                topic="ArtifactGuard",
                message="Aufgaben gelten nur mit verifizierter Evidence als wirklich abgeschlossen.",
            ),
            TutorialStep(
                id="nagacore-codecompass",
                topic="CodeCompass",
                message="CodeCompass liefert die erklaerbare Kartenbasis fuer Territorien und Abhaengigkeiten.",
            ),
        )

    def render_payload(self, *, surface: str) -> dict[str, object]:
        return {
            "surface": surface,
            "guide_only": True,
            "policy_authority": False,
            "steps": [step.to_dict() for step in self.tutorial_steps()],
        }
