RULES = {
    "generic_phrase": "Remove generic filler and state the concrete point directly.",
    "missing_concrete_example": "Support important claims with one bounded concrete example.",
    "vague_attribution": "Name an available evidence source or explicitly mark uncertainty.",
    "structure_mismatch": "Use the structure appropriate for the requested content kind.",
    "style_uniformity": "Vary sentence rhythm only where it improves readability.",
    "source_unverified": "Do not invent details; use only provided evidence references.",
    "unsupported_specific_claim": "Remove unsupported specificity or mark it as an unverified hypothesis.",
}


def rules_for(reason_codes: list[str]) -> list[str]:
    return [RULES[code] for code in reason_codes if code in RULES]
