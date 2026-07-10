from __future__ import annotations

from typing import Any


CONTENT_MODES: dict[str, dict[str, Any]] = {
    "factual_documentary": {
        "label_nl": "Feitelijke documentaire",
        "description_nl": "Een conventionele documentaire op basis van gecontroleerde feiten en duidelijke onzekerheid.",
        "purpose": "Create a conventional evidence-based documentary.",
        "claim_classes": ["verified_fact", "single_source_claim", "official_explanation", "unanswered_question"],
        "research_policy": "Prioritize validated and corroborated facts; distinguish single-source evidence and uncertainty; avoid unsupported conclusions.",
    },
    "investigative_documentary": {
        "label_nl": "Onderzoeksdocumentaire",
        "description_nl": "Onderzoekt controverses, verklaringen, tegenstrijdigheden en onbeantwoorde vragen met bronvermelding.",
        "purpose": "Investigate controversial events, allegations, disputed narratives, suspicious circumstances, and competing explanations.",
        "claim_classes": ["verified_fact", "single_source_claim", "allegation", "witness_statement", "official_explanation", "alternative_explanation", "disputed_claim", "interpretation", "unanswered_question"],
        "research_policy": "Retain attributed allegations, witness statements, disputes, minority viewpoints, contradictions, and competing explanations; label each epistemic status explicitly.",
    },
    "theory_conspiracy": {
        "label_nl": "Theorie / complot",
        "description_nl": "Onderzoekt theorieën en alternatieve verklaringen met ruimte voor argumenten én tegenargumenten.",
        "purpose": "Explore conspiracy theories, alternative explanations, hidden-power theories, controversial interpretations, and disputed narratives.",
        "claim_classes": ["verified_fact", "single_source_claim", "allegation", "witness_statement", "official_explanation", "alternative_explanation", "disputed_claim", "interpretation", "speculation", "unanswered_question"],
        "research_policy": "Seriously investigate theory origins, proponents, cited patterns, motives, connections, anomalies, counterarguments, and conventional explanations. Never convert speculation into fact or suppress a theory solely because authorities reject it; preserve attribution.",
    },
}

CLAIM_CLASSIFICATIONS = tuple({item for mode in CONTENT_MODES.values() for item in mode["claim_classes"]})


def normalize_content_mode(value: str | None) -> str:
    return value if value in CONTENT_MODES else "factual_documentary"


def content_mode(value: str | None) -> dict[str, Any]:
    return CONTENT_MODES[normalize_content_mode(value)]


def mode_prompt(value: str | None) -> str:
    mode = content_mode(value)
    return (
        f"CONTENT MODE: {mode['label_nl']} ({normalize_content_mode(value)}). {mode['purpose']} "
        f"Allowed evidence classifications: {', '.join(mode['claim_classes'])}. {mode['research_policy']} "
        "Never silently upgrade allegations, interpretations, speculation, or disputed claims into verified facts."
    )
