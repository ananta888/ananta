"""Closed contracts for optional, Hub-authorized research post-training."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ananta_contracts.research_training import ResearchTrainingContractError, canonical_digest, require_id


@dataclass(frozen=True, slots=True)
class ResearchRewardSpecV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-reward.v1"

    schema: str
    reward_id: str
    reward_version: str
    provider: str
    maximum_absolute_reward: float
    redact_rollouts: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchRewardSpecV1:
        if set(value) != {
            "schema",
            "reward_id",
            "reward_version",
            "provider",
            "maximum_absolute_reward",
            "redact_rollouts",
        }:
            raise ResearchTrainingContractError("research_reward_fields_invalid")
        maximum = value.get("maximum_absolute_reward")
        if (
            value.get("schema") != cls.SCHEMA
            or not isinstance(maximum, (int, float))
            or isinstance(maximum, bool)
            or not math.isfinite(float(maximum))
            or not 0 < float(maximum) <= 1_000
            or not isinstance(value.get("redact_rollouts"), bool)
        ):
            raise ResearchTrainingContractError("research_reward_invalid")
        return cls(
            schema=cls.SCHEMA,
            reward_id=require_id(value.get("reward_id"), "reward_id"),
            reward_version=require_id(value.get("reward_version"), "reward_version"),
            provider=require_id(value.get("provider"), "reward_provider"),
            maximum_absolute_reward=float(maximum),
            redact_rollouts=bool(value["redact_rollouts"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResearchRlConfigV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-rl-config.v1"

    schema: str
    algorithm: str
    samples_per_prompt: int
    maximum_new_tokens: int
    temperature: float
    learning_rate: float
    maximum_steps: int
    seed: int
    reward: ResearchRewardSpecV1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchRlConfigV1:
        if set(value) != {
            "schema",
            "algorithm",
            "samples_per_prompt",
            "maximum_new_tokens",
            "temperature",
            "learning_rate",
            "maximum_steps",
            "seed",
            "reward",
        } or value.get("schema") != cls.SCHEMA:
            raise ResearchTrainingContractError("research_rl_config_fields_invalid")
        if value.get("algorithm") not in {"reinforce_v1", "group_relative_v1"}:
            raise ResearchTrainingContractError("research_rl_algorithm_invalid")
        integers = {
            "samples_per_prompt": (1, 64),
            "maximum_new_tokens": (1, 2048),
            "maximum_steps": (1, 1_000_000),
            "seed": (0, (1 << 31) - 1),
        }
        normalized_ints: dict[str, int] = {}
        for name, (minimum, maximum) in integers.items():
            raw = value.get(name)
            if not isinstance(raw, int) or isinstance(raw, bool) or not minimum <= raw <= maximum:
                raise ResearchTrainingContractError(f"research_rl_{name}_invalid")
            normalized_ints[name] = raw
        temperature = value.get("temperature")
        learning_rate = value.get("learning_rate")
        if (
            not isinstance(temperature, (int, float))
            or isinstance(temperature, bool)
            or not 0 < float(temperature) <= 10
            or not isinstance(learning_rate, (int, float))
            or isinstance(learning_rate, bool)
            or not 0 < float(learning_rate) <= 1
            or not isinstance(value.get("reward"), Mapping)
        ):
            raise ResearchTrainingContractError("research_rl_numeric_invalid")
        return cls(
            schema=cls.SCHEMA,
            algorithm=str(value["algorithm"]),
            temperature=float(temperature),
            learning_rate=float(learning_rate),
            reward=ResearchRewardSpecV1.from_mapping(value["reward"]),
            **normalized_ints,
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "reward": self.reward.to_dict()}


__all__ = ["ResearchRewardSpecV1", "ResearchRlConfigV1"]
