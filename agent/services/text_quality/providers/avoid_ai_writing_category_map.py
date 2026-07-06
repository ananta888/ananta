KNOWN_UPSTREAM_TYPES = frozenset(
    {
        "tier1",
        "tier2",
        "tier3",
        "transition",
        "template-phrase",
        "tier3-phrase",
        "tier3-phrase-cluster",
        "chatbot",
        "sycophantic",
        "acknowledgment-loop",
        "filler",
        "hollow-intensifier",
        "generic-conclusion",
        "social-cta-closer",
        "future-narrative",
        "lets-construction",
        "reasoning-artifact",
        "significance-inflation",
        "novelty-inflation",
        "real-actual-inflation",
        "vague-attribution",
        "emotional-flatline",
        "cutoff-disclaimer",
        "false-concession",
        "rhetorical-question",
        "formulaic-opener",
        "confidence-calibration",
        "hedge-stack",
        "parenthetical-hedge",
        "hashtag-stuff",
        "bullet-np-list",
        "title-case-header",
        "em-dash",
        "formatting",
        "uniformity",
        "low-ttr",
        "ai-placeholder",
        "ai-citation-markup",
        "ai-utm-source",
        "smart-punct-signature",
        "punct-distribution",
        "fnword-trigram-entropy",
        "cross-para-burstiness",
        "normalization-flag",
    }
)

_MAP = {
    "transition": "overused_transition",
    "generic-conclusion": "generic_phrase",
    "vague-attribution": "vague_attribution",
    "uniformity": "style_uniformity",
    "cross-para-burstiness": "style_uniformity",
    "bullet-np-list": "structure_mismatch",
}


def map_category(category: str, *, strict: bool = True) -> str:
    normalized = str(category or "").strip()
    if normalized not in KNOWN_UPSTREAM_TYPES:
        if strict:
            raise ValueError(f"upstream_unknown:{normalized}")
        return "upstream_unknown"
    return _MAP.get(normalized, "generic_phrase")
