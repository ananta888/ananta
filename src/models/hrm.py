"""
Hierarchical Reasoning Model (HRM)

A lightweight, dependency-free reference implementation of a hierarchical
recurrent architecture with two coupled modules:
  - HighLevelModule: performs slow, abstract planning across a few macro steps
  - LowLevelModule: performs fast, detailed computations across micro steps

This implementation focuses on clarity and testability within this repository,
not on performance. It demonstrates how sequential reasoning can be executed in
one forward() call without explicit supervision of intermediate steps.

The model supports simple arithmetic word problems embedded in text, such as:
  - "add 2 and 3" => 5
  - "sum 10, 20, and 7" => 37
  - "multiply 4 and 5" => 20

It returns both an answer and a reasoning trace capturing high- and low-level
states across steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, Optional
import re


@dataclass
class HRMConfig:
    # Number of macro (high-level) steps and micro (low-level) steps per segment
    max_macro_steps: int = 3
    max_micro_steps: int = 8
    # Whether to include detailed traces for debugging/inspection
    trace: bool = True


@dataclass
class HighLevelState:
    step: int = 0
    objective: Optional[str] = None
    segments: List[str] = field(default_factory=list)


@dataclass
class LowLevelState:
    step: int = 0
    buffer: List[str] = field(default_factory=list)


class HighLevelModule:
    """High-level planner that parses the task and proposes a plan.

    It updates at a slower timescale (few macro steps), progressively refining:
      - the objective (operation to perform)
      - the segments (pieces of input for low-level processing)
    """

    def step(self, text: str, state: HighLevelState) -> Tuple[HighLevelState, Dict[str, Any]]:
        obs: Dict[str, Any] = {}
        s = HighLevelState(**vars(state))
        s.step += 1

        # 1) Infer objective (very simple heuristic parsing)
        lowered = text.lower()
        if s.objective is None:
            if any(w in lowered for w in ("add", "sum", "plus", "+")):
                s.objective = "add"
            elif any(w in lowered for w in ("multiply", "product", "times", "*", "x")):
                s.objective = "multiply"
            else:
                s.objective = "unknown"
        obs["objective"] = s.objective

        # 2) Identify segments (numbers and separators); keep it simple but deterministic
        if not s.segments:
            # split by conjunctions/commas/and/or
            # retain only potential numeric-containing pieces
            raw_parts = re.split(r"[;,]| and | plus | with | by | times | x |\+|\*|\s{2,}", lowered)
            parts = [p.strip() for p in raw_parts if p and re.search(r"\d", p)]
            # limit segments to avoid runaway
            s.segments = parts[:8]
        obs["segments"] = list(s.segments)

        return s, obs


class LowLevelModule:
    """Low-level executor that processes a segment across a few micro steps.

    For demonstration, it extracts integers from the segment and aggregates
    according to the high-level objective.
    """

    def step(self, segment: str, objective: str, state: LowLevelState) -> Tuple[LowLevelState, Dict[str, Any]]:
        obs: Dict[str, Any] = {}
        s = LowLevelState(**vars(state))
        s.step += 1

        # micro update: push tokenized numbers into buffer
        nums = re.findall(r"[-+]?\d+", segment)
        if nums:
            s.buffer.extend(nums)
        obs["buffer"] = list(s.buffer)
        obs["last_segment"] = segment
        obs["objective"] = objective
        return s, obs

    @staticmethod
    def reduce_buffer(buffer: List[str], objective: str) -> Optional[int]:
        vals = [int(x) for x in buffer]
        if not vals:
            return None
        if objective == "add":
            total = 0
            for v in vals:
                total += v
            return total
        if objective == "multiply":
            prod = 1
            for v in vals:
                prod *= v
            return prod
        # fallback: if unknown, return the first value
        return vals[0]


class HierarchicalReasoningModel:
    """Coupled high- and low-level recurrent modules executed in one forward pass."""

    def __init__(self, config: Optional[HRMConfig] = None) -> None:
        self.config = config or HRMConfig()
        self.hi = HighLevelModule()
        self.lo = LowLevelModule()

    def forward(self, text: str) -> Dict[str, Any]:
        """Run hierarchical reasoning over the input text.

        Returns a dict with keys:
          - answer: Optional[int]
          - objective: str
          - trace: Dict with high_level and low_level step-wise observations
        """
        trace: Dict[str, Any] = {"high_level": [], "low_level": []}

        # High-level recurrent refinement
        h_state = HighLevelState()
        for _ in range(self.config.max_macro_steps):
            h_state, h_obs = self.hi.step(text, h_state)
            if self.config.trace:
                trace["high_level"].append({"step": h_state.step, **h_obs})
            # Early stop if objective and segments are set and stable enough
            if h_state.objective and h_state.segments:
                break

        # Low-level fast computation across segments
        l_state = LowLevelState()
        max_micro = self.config.max_micro_steps
        steps = 0
        for seg in h_state.segments:
            if steps >= max_micro:
                break
            l_state, l_obs = self.lo.step(seg, h_state.objective or "unknown", l_state)
            steps += 1
            if self.config.trace:
                trace["low_level"].append({"step": l_state.step, **l_obs})

        # Reduce low-level buffer according to high-level objective
        answer = self.lo.reduce_buffer(l_state.buffer, h_state.objective or "unknown")

        return {
            "answer": answer,
            "objective": h_state.objective or "unknown",
            "trace": trace,
        }

    # alias for convenience
    __call__ = forward
