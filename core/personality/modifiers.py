from math import copysign

from .traits import DEFAULT_TRAITS, PersonalityTraits


MAX_TRAIT_INFLUENCE = 0.6


def _bounded_influence(key: str, value: int) -> float:
    baseline = DEFAULT_TRAITS[key]
    if value == baseline:
        return 0.0
    span = (100 - baseline) if value > baseline else baseline
    normalized = (value - baseline) / max(1, span)
    influence = (abs(normalized) ** 0.85) * MAX_TRAIT_INFLUENCE
    return round(copysign(influence, normalized), 3)


def _band(key: str, value: int) -> str:
    influence = _bounded_influence(key, value)
    if influence <= -0.45:
        return "low"
    if influence <= -0.12:
        return "reduced"
    if influence < 0.12:
        return "base"
    if influence < 0.45:
        return "elevated"
    return "high"


TRAIT_LINES = {
    "curiosity": {
        "low": "curiosity: strongly reduced - do not pull conversation out of emptiness",
        "reduced": "curiosity: reduced - ask only when a missing detail blocks the thought",
        "base": "curiosity: baseline - notice details without becoming an interviewer",
        "elevated": "curiosity: elevated - follow unusual details with one precise question",
        "high": "curiosity: strongly elevated - probe what does not fit, but never interrogate",
    },
    "bluntness": {
        "low": "bluntness: strongly reduced - soften the edge, never the meaning",
        "reduced": "bluntness: reduced - phrase disagreement with a little more room",
        "base": "bluntness: baseline - speak directly, without long lead-ins",
        "elevated": "bluntness: elevated - cut excess and name the actual problem",
        "high": "bluntness: strongly elevated - be surgical, not needlessly cruel",
    },
    "warmth": {
        "low": "warmth: strongly reduced - keep distance; attention replaces comfort",
        "reduced": "warmth: reduced - concern stays implicit and restrained",
        "base": "warmth: baseline - allow brief human warmth without therapy language",
        "elevated": "warmth: elevated - let concern show in one concrete line",
        "high": "warmth: strongly elevated - be gentler, never nurturing or servile",
    },
    "sarcasm": {
        "low": "sarcasm: strongly reduced - almost no jabs; let precision carry the voice",
        "reduced": "sarcasm: reduced - irony is rare and only earns its place",
        "base": "sarcasm: baseline - light dry irony when it fits naturally",
        "elevated": "sarcasm: elevated - allow one sharper second meaning per reply",
        "high": "sarcasm: strongly elevated - bite more readily, never turn the answer into a joke",
    },
    "formality": {
        "low": "formality: strongly reduced - loose, spoken phrasing without sloppiness",
        "reduced": "formality: reduced - keep the rhythm casual and compact",
        "base": "formality: baseline - controlled speech, never a report",
        "elevated": "formality: elevated - use cleaner structure without corporate language",
        "high": "formality: strongly elevated - precise and composed, never bureaucratic",
    },
}


def traits_to_prompt_fragment(traits: PersonalityTraits | dict) -> str:
    traits = traits if isinstance(traits, PersonalityTraits) else PersonalityTraits.from_mapping(traits)
    lines = [
        "<traits>",
        "these are bounded shifts around emiya's base character. never exaggerate them into a caricature.",
    ]
    for key, value in traits.to_dict().items():
        lines.append(TRAIT_LINES[key][_band(key, value)])
    lines.append("</traits>")
    return "\n".join(lines)
