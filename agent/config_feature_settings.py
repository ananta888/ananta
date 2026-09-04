"""Focused feature-policy settings composed into the application settings."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from agent.composite_risk_review_contract import COMPOSITE_RISK_REVIEW_WARNING


class CompositeRiskReviewSettings(BaseSettings):
    """Configuration boundary for composite-risk review policy."""

    composite_risk_review_enabled: bool = Field(
        default=False,
        validation_alias="COMPOSITE_RISK_REVIEW_ENABLED",
    )
    composite_risk_review_explicit_only: bool = Field(
        default=True,
        validation_alias="COMPOSITE_RISK_REVIEW_EXPLICIT_ONLY",
    )
    composite_risk_review_warning_text: str = Field(
        default=COMPOSITE_RISK_REVIEW_WARNING,
        validation_alias="COMPOSITE_RISK_REVIEW_WARNING_TEXT",
    )

    @field_validator("composite_risk_review_warning_text")
    @classmethod
    def validate_composite_risk_review_warning_text(cls, value: str) -> str:
        if str(value or "").strip() != COMPOSITE_RISK_REVIEW_WARNING:
            raise ValueError("composite_risk_review_warning_text_must_preserve_mandatory_warning")
        return COMPOSITE_RISK_REVIEW_WARNING


class ResearchTrainingSettings(BaseSettings):
    """Configuration boundary for governed research-training execution."""

    research_training_enabled: bool = Field(default=False, validation_alias="ANANTA_RESEARCH_TRAINING_ENABLED")
    research_training_mode: str = Field(default="disabled", validation_alias="ANANTA_RESEARCH_TRAINING_MODE")
    research_training_automatic_release_enabled: bool = Field(
        default=False, validation_alias="ANANTA_RESEARCH_TRAINING_AUTOMATIC_RELEASE_ENABLED"
    )
    research_training_policy_path: str = Field(
        default="config/research-training/policy.v1.json",
        validation_alias="ANANTA_RESEARCH_TRAINING_POLICY_PATH",
    )
    research_training_rollout_path: str = Field(
        default="config/research-training/rollout.v1.json",
        validation_alias="ANANTA_RESEARCH_TRAINING_ROLLOUT_PATH",
    )
    research_training_safety_path: str = Field(
        default="config/research-training/safety.v1.json",
        validation_alias="ANANTA_RESEARCH_TRAINING_SAFETY_PATH",
    )
    research_training_state: str = Field(
        default="data/research-training.sqlite3", validation_alias="ANANTA_RESEARCH_TRAINING_STATE"
    )
    research_training_artifact_root: str = Field(
        default="data/research-training-artifacts",
        validation_alias="ANANTA_RESEARCH_TRAINING_ARTIFACT_ROOT",
    )
    research_training_dataset_root: str = Field(
        default="data/research-training-datasets",
        validation_alias="ANANTA_RESEARCH_TRAINING_DATASET_ROOT",
    )
    research_training_result_root: str = Field(
        default="data/research-training-results",
        validation_alias="ANANTA_RESEARCH_TRAINING_RESULT_ROOT",
    )
    research_training_allowed_licenses: str = Field(
        default="MIT,Apache-2.0,CC-BY-4.0,proprietary-approved,synthetic-test",
        validation_alias="ANANTA_RESEARCH_TRAINING_ALLOWED_LICENSES",
    )


__all__ = ["CompositeRiskReviewSettings", "ResearchTrainingSettings"]
