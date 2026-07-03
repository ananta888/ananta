from __future__ import annotations
import fnmatch, time, uuid
from dataclasses import dataclass, field
from typing import Any

RISK_DIMENSIONS = ["security", "data_loss", "api_breakage", "test_gap", "runtime_path_criticality",
                   "config_change", "migration_or_schema_change", "dependency_change", "policy_change"]

AUTO_HIGH_RISK_PATHS = [".github/", ".ci/", "docker-compose", "Dockerfile",
    "requirements.txt", "package.json", "pyproject.toml",
    "alembic/", "migrations/", "schema.sql"]

DIM_WEIGHTS = {"security": 2.0, "data_loss": 2.0, "api_breakage": 1.5}

@dataclass
class RiskDimension:
    name: str
    score: int  # 0-100
    evidence: str
    auto_elevated: bool = False

@dataclass
class RiskReport:
    report_id: str
    run_id: str
    diff_artifact_ref: str | None
    total_score: int
    risk_verdict: str
    dimensions: list[RiskDimension]
    recommended_verifications: list[str]
    auto_elevated_by_paths: list[str]
    missing_test_coverage_risk: bool
    created_at: float
    def as_dict(self) -> dict[str, Any]:
        return {"report_id": self.report_id, "total_score": self.total_score,
                "risk_verdict": self.risk_verdict, "auto_elevated_by_paths": self.auto_elevated_by_paths}

class RiskAnalystExpert:
    def analyze(self, *, run_id: str, changed_files: list[str], diff_summary: str = "",
               has_tests: bool = True, diff_artifact_ref: str | None = None) -> RiskReport:
        elevated = self.check_auto_elevated_paths(changed_files)
        dims = []
        for name in RISK_DIMENSIONS:
            score = 10
            elevated_dim = any(name in ["security", "config_change", "policy_change"] and p in elevated for p in elevated) or bool(elevated)
            if elevated_dim and name in ["security", "config_change"]:
                score = 70
            if name == "test_gap" and not has_tests:
                score = 60
            dims.append(RiskDimension(name=name, score=score, evidence="auto-analysis", auto_elevated=elevated_dim))
        total = self.score_total(dims)
        verdict = self.verdict_from_score(total)
        return RiskReport(
            report_id=str(uuid.uuid4()), run_id=run_id, diff_artifact_ref=diff_artifact_ref,
            total_score=total, risk_verdict=verdict, dimensions=dims,
            recommended_verifications=["run tests", "review diff"] + (["security review"] if elevated else []),
            auto_elevated_by_paths=elevated, missing_test_coverage_risk=not has_tests, created_at=time.time(),
        )

    def score_total(self, dimensions: list[RiskDimension]) -> int:
        total_weight = sum(DIM_WEIGHTS.get(d.name, 1.0) for d in dimensions)
        weighted = sum(d.score * DIM_WEIGHTS.get(d.name, 1.0) for d in dimensions)
        return int(weighted / total_weight) if total_weight > 0 else 0

    def verdict_from_score(self, score: int) -> str:
        if score >= 80: return "critical"
        if score >= 60: return "high"
        if score >= 30: return "medium"
        return "low"

    def check_auto_elevated_paths(self, changed_files: list[str]) -> list[str]:
        elevated = []
        for f in changed_files:
            for pattern in AUTO_HIGH_RISK_PATHS:
                if pattern in f:
                    elevated.append(f)
                    break
        return elevated
