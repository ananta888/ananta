"""Public constants for the optional Composite Risk Review feature."""

COMPOSITE_RISK_REVIEW_WARNING = (
    "Composite Risk Review ist nur ein optionaler Risiko-Hinweis. Ananta kann "
    "keine vollstaendige Absichtserkennung ueber beliebig zerlegte Aufgaben "
    "garantieren. Keine Warnung bedeutet nicht, dass ein Goal, eine Task-Kette "
    "oder ein Artefakt sicher ist."
)

COMPOSITE_RISK_REVIEW_SCHEMA = "composite_risk_review.v1"

__all__ = ["COMPOSITE_RISK_REVIEW_SCHEMA", "COMPOSITE_RISK_REVIEW_WARNING"]
