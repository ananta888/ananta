from __future__ import annotations
import json, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class WorkflowKind(str, Enum):
    CONTEXT_RETRIEVAL = "context_retrieval"
    PR_REVIEW = "pr_review"
    CODE_GENERATION = "code_generation"
    RISK_ANALYSIS = "risk_analysis"
    TEST_SELECTION = "test_selection"
    DELEGATION_PLANNING = "delegation_planning"
    FULL_PIPELINE = "full_pipeline"

class ProviderMode(str, Enum):
    LOCAL_ONLY = "local_only"
    FAKE = "fake"
    AUGMENT = "augment"
    HYBRID = "hybrid"

class EvalVerdict(str, Enum):
    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"
    SKIPPED = "skipped"
    NO_GOLD_SET = "no_gold_set"

@dataclass
class GoldExpectation:
    expected_files: list[str] = field(default_factory=list)
    expected_symbols: list[str] = field(default_factory=list)
    min_score: float = 0.5
    max_latency_ms: int = 10000
    required_keywords: list[str] = field(default_factory=list)
    rubric_notes: str = ""

@dataclass
class WorkflowSpec:
    workflow_id: str
    name: str
    kind: WorkflowKind
    query: str
    provider_mode: ProviderMode
    gold: GoldExpectation | None
    max_cost_units: float = 1.0
    requires_approval: bool = False
    tags: list[str] = field(default_factory=list)

@dataclass
class BenchmarkSample:
    sample_id: str
    workflow_id: str
    provider_mode: str
    query: str
    retrieved_files: list[str]
    retrieved_symbols: list[str]
    scores: list[float]
    latency_ms: int
    cost_units: float
    approval_gates_triggered: int
    test_coverage_pct: float | None
    error: str | None
    created_at: float

@dataclass
class EvalResult:
    verdict: EvalVerdict
    score: float           # 0.0-1.0
    latency_ok: bool
    cost_ok: bool
    files_hit_count: int
    files_missed: list[str]
    symbols_hit_count: int
    notes: str

@dataclass
class WorkflowResult:
    result_id: str
    workflow_id: str
    workflow_name: str
    kind: WorkflowKind
    provider_mode: str
    sample: BenchmarkSample
    eval_result: EvalResult
    run_duration_ms: int
    created_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "kind": self.kind.value,
            "provider_mode": self.provider_mode,
            "verdict": self.eval_result.verdict.value,
            "score": self.eval_result.score,
            "latency_ms": self.sample.latency_ms,
            "cost_units": self.sample.cost_units,
            "files_hit": self.eval_result.files_hit_count,
            "files_missed": self.eval_result.files_missed,
            "approval_gates": self.sample.approval_gates_triggered,
            "error": self.sample.error,
            "created_at": self.created_at,
        }

@dataclass
class BenchmarkRun:
    run_id: str
    name: str
    results: list[WorkflowResult]
    total_workflows: int
    passed: int
    partial: int
    failed: int
    skipped: int
    avg_score: float
    total_latency_ms: int
    total_cost_units: float
    created_at: float

    def pass_rate(self) -> float:
        if self.total_workflows == 0: return 0.0
        return self.passed / self.total_workflows

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "name": self.name,
            "total_workflows": self.total_workflows,
            "passed": self.passed, "partial": self.partial,
            "failed": self.failed, "skipped": self.skipped,
            "pass_rate": round(self.pass_rate(), 3),
            "avg_score": round(self.avg_score, 3),
            "total_latency_ms": self.total_latency_ms,
            "total_cost_units": round(self.total_cost_units, 4),
            "created_at": self.created_at,
            "results": [r.as_dict() for r in self.results],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Benchmark Run: {self.name}",
            f"",
            f"**Run ID:** `{self.run_id}`  ",
            f"**Pass Rate:** {self.pass_rate():.0%}  ",
            f"**Avg Score:** {self.avg_score:.2f}  ",
            f"**Total Latency:** {self.total_latency_ms}ms  ",
            f"**Total Cost:** {self.total_cost_units:.4f} units",
            "",
            "## Results",
            "",
            "| Workflow | Kind | Provider | Verdict | Score | Latency | Files Hit |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in self.results:
            lines.append(
                f"| {r.workflow_name} | {r.kind.value} | {r.provider_mode} | "
                f"{r.eval_result.verdict.value} | {r.eval_result.score:.2f} | "
                f"{r.sample.latency_ms}ms | {r.eval_result.files_hit_count} |"
            )
        lines += ["", "## Misses"]
        for r in self.results:
            if r.eval_result.files_missed:
                lines.append(f"- **{r.workflow_name}**: {', '.join(r.eval_result.files_missed)}")
        return "\n".join(lines)

class BenchmarkSuiteRunner:
    """
    Runs benchmark workflows using fake or real providers.
    External providers (Augment) are optional; fake mode is the default.
    """

    def __init__(self, *, fake_retrieval: dict[str, list[str]] | None = None) -> None:
        self._fake_retrieval = fake_retrieval or {}  # query → list of files
        self._workflows: list[WorkflowSpec] = []

    def register_workflow(self, spec: WorkflowSpec) -> None:
        self._workflows.append(spec)

    def run_all(self, *, name: str = "benchmark") -> BenchmarkRun:
        results = []
        for spec in self._workflows:
            result = self._run_one(spec)
            results.append(result)
        return self._build_run(name, results)

    def run_workflow(self, workflow_id: str) -> WorkflowResult | None:
        spec = next((w for w in self._workflows if w.workflow_id == workflow_id), None)
        if spec is None:
            return None
        return self._run_one(spec)

    def _run_one(self, spec: WorkflowSpec) -> WorkflowResult:
        start = time.time()
        try:
            sample = self._simulate_retrieval(spec)
        except Exception as e:
            sample = BenchmarkSample(
                sample_id=str(uuid.uuid4())[:8], workflow_id=spec.workflow_id,
                provider_mode=spec.provider_mode.value, query=spec.query,
                retrieved_files=[], retrieved_symbols=[], scores=[],
                latency_ms=0, cost_units=0.0, approval_gates_triggered=0,
                test_coverage_pct=None, error=str(e), created_at=time.time(),
            )

        eval_result = self._evaluate(spec, sample)
        duration_ms = int((time.time() - start) * 1000)

        return WorkflowResult(
            result_id=str(uuid.uuid4())[:8],
            workflow_id=spec.workflow_id,
            workflow_name=spec.name,
            kind=spec.kind,
            provider_mode=spec.provider_mode.value,
            sample=sample,
            eval_result=eval_result,
            run_duration_ms=duration_ms,
            created_at=time.time(),
        )

    def _simulate_retrieval(self, spec: WorkflowSpec) -> BenchmarkSample:
        retrieved = self._fake_retrieval.get(spec.query, [])
        scores = [0.8] * len(retrieved) if retrieved else []
        latency = 50 if spec.provider_mode == ProviderMode.FAKE else 200
        cost = 0.001 * len(retrieved) if spec.provider_mode != ProviderMode.FAKE else 0.0
        return BenchmarkSample(
            sample_id=str(uuid.uuid4())[:8],
            workflow_id=spec.workflow_id,
            provider_mode=spec.provider_mode.value,
            query=spec.query,
            retrieved_files=list(retrieved),
            retrieved_symbols=[],
            scores=scores,
            latency_ms=latency,
            cost_units=cost,
            approval_gates_triggered=1 if spec.requires_approval else 0,
            test_coverage_pct=None,
            error=None,
            created_at=time.time(),
        )

    def _evaluate(self, spec: WorkflowSpec, sample: BenchmarkSample) -> EvalResult:
        if sample.error:
            return EvalResult(EvalVerdict.FAIL, 0.0, False, True, 0, [], 0, f"error: {sample.error}")

        if spec.gold is None:
            return EvalResult(EvalVerdict.NO_GOLD_SET, 0.5, True, True, len(sample.retrieved_files),
                             [], len(sample.retrieved_symbols), "no gold set defined")

        gold = spec.gold
        latency_ok = sample.latency_ms <= gold.max_latency_ms
        cost_ok = sample.cost_units <= spec.max_cost_units

        expected = set(gold.expected_files)
        retrieved = set(sample.retrieved_files)
        hits = expected & retrieved
        missed = list(expected - retrieved)
        files_hit = len(hits)

        sym_expected = set(gold.expected_symbols)
        sym_hit = len(sym_expected & set(sample.retrieved_symbols))

        if expected:
            precision = files_hit / max(len(retrieved), 1)
            recall = files_hit / len(expected)
            score = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        else:
            score = 0.8 if not sample.error else 0.0

        if score >= 0.8 and not missed:
            verdict = EvalVerdict.PASS
        elif score >= 0.4:
            verdict = EvalVerdict.PARTIAL
        else:
            verdict = EvalVerdict.FAIL

        return EvalResult(
            verdict=verdict, score=round(score, 3), latency_ok=latency_ok, cost_ok=cost_ok,
            files_hit_count=files_hit, files_missed=missed,
            symbols_hit_count=sym_hit,
            notes=f"F1={score:.2f}, {files_hit}/{len(expected)} files hit",
        )

    def _build_run(self, name: str, results: list[WorkflowResult]) -> BenchmarkRun:
        passed = sum(1 for r in results if r.eval_result.verdict == EvalVerdict.PASS)
        partial = sum(1 for r in results if r.eval_result.verdict == EvalVerdict.PARTIAL)
        failed = sum(1 for r in results if r.eval_result.verdict == EvalVerdict.FAIL)
        skipped = sum(1 for r in results if r.eval_result.verdict in (EvalVerdict.SKIPPED, EvalVerdict.NO_GOLD_SET))
        total_latency = sum(r.sample.latency_ms for r in results)
        total_cost = sum(r.sample.cost_units for r in results)
        avg_score = sum(r.eval_result.score for r in results) / max(len(results), 1)
        return BenchmarkRun(
            run_id=str(uuid.uuid4()), name=name, results=results,
            total_workflows=len(results), passed=passed, partial=partial,
            failed=failed, skipped=skipped, avg_score=round(avg_score, 3),
            total_latency_ms=total_latency, total_cost_units=round(total_cost, 6),
            created_at=time.time(),
        )

def make_workflow(*, name: str, kind: WorkflowKind, query: str,
                 expected_files: list[str] | None = None,
                 provider_mode: ProviderMode = ProviderMode.FAKE,
                 requires_approval: bool = False) -> WorkflowSpec:
    gold = GoldExpectation(expected_files=list(expected_files or [])) if expected_files else None
    return WorkflowSpec(
        workflow_id=str(uuid.uuid4())[:8], name=name, kind=kind, query=query,
        provider_mode=provider_mode, gold=gold, requires_approval=requires_approval,
    )
