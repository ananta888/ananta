from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from typing import Any

@dataclass
class DispatchStep:
    step_id: str
    description: str
    expert_id: str
    required_tools: list[str]
    context_artifacts_needed: list[str]
    estimated_risk: str  # "low"|"medium"|"high"
    approval_gates: list[str]
    depends_on: list[str]

@dataclass
class DispatchPlan:
    plan_id: str
    run_id: str
    goal_summary: str
    steps: list[DispatchStep]
    open_questions: list[str]
    risks: list[str]
    estimated_approval_gates: list[str]
    plan_status: str  # "draft"|"pending_approval"|"approved"|"rejected"
    created_at: float

    def has_open_questions(self) -> bool: return bool(self.open_questions)
    def as_dict(self) -> dict[str, Any]: return {
        "plan_id": self.plan_id, "run_id": self.run_id, "goal_summary": self.goal_summary,
        "steps": len(self.steps), "open_questions": self.open_questions,
        "risks": self.risks, "plan_status": self.plan_status,
    }

class WorkDispatcherExpert:
    UNCLEAR_GOAL_THRESHOLD = 10

    def create_plan(self, *, run_id: str, goal_text: str,
                   available_experts: list[str] | None = None,
                   policy_constraints: dict | None = None) -> DispatchPlan:
        goal_text = (goal_text or "").strip()
        steps: list[DispatchStep] = []
        open_questions: list[str] = []

        # Unclear goal: short or no verb-like words
        is_unclear = len(goal_text) < self.UNCLEAR_GOAL_THRESHOLD or not any(
            v in goal_text.lower() for v in ["implement", "add", "fix", "create", "update",
                "remove", "refactor", "analyze", "review", "test", "deploy", "build",
                "schreib", "erstell", "fix", "analysier", "implementier"]
        )

        if is_unclear:
            steps.append(DispatchStep(
                step_id=str(uuid.uuid4())[:8], description="Analyse und Klärung des Ziels",
                expert_id="code_context_analyst", required_tools=["read_file", "plan_context"],
                context_artifacts_needed=[], estimated_risk="low",
                approval_gates=["submit_analysis"], depends_on=[],
            ))
            open_questions.append(f"Ziel '{goal_text}' ist zu kurz oder unklar. Bitte spezifizieren.")
        else:
            steps.append(DispatchStep(
                step_id=str(uuid.uuid4())[:8], description=f"Kontext sammeln für: {goal_text[:80]}",
                expert_id="code_context_analyst", required_tools=["plan_context", "search_symbols"],
                context_artifacts_needed=[], estimated_risk="low", approval_gates=[], depends_on=[],
            ))
            steps.append(DispatchStep(
                step_id=str(uuid.uuid4())[:8], description="Implementierung durchführen",
                expert_id="pr_author", required_tools=["read_file", "diff_apply_proposal"],
                context_artifacts_needed=["context_bundle"], estimated_risk="medium",
                approval_gates=["apply_diff", "submit_plan"], depends_on=[steps[0].step_id],
            ))

        return DispatchPlan(
            plan_id=str(uuid.uuid4()), run_id=run_id, goal_summary=goal_text[:200],
            steps=steps, open_questions=open_questions, risks=["review required before apply"],
            estimated_approval_gates=["submit_plan", "apply_diff"],
            plan_status="draft", created_at=time.time(),
        )

    def validate_plan(self, plan: DispatchPlan) -> list[str]:
        errors = []
        if not plan.steps: errors.append("Plan has no steps")
        if not plan.goal_summary: errors.append("Missing goal summary")
        return errors

    def add_clarification_step(self, plan: DispatchPlan, question: str) -> DispatchPlan:
        plan.open_questions.append(question)
        return plan
