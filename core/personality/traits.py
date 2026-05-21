import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "personality.json"
PRESETS_PATH = ROOT / "config" / "personality_presets.json"
TRAIT_KEYS = ("curiosity", "bluntness", "warmth", "sarcasm", "formality")

DEFAULT_TRAITS = {
    "curiosity": 70,
    "bluntness": 80,
    "warmth": 40,
    "sarcasm": 60,
    "formality": 20,
}

BUILTIN_PERSONALITY_PRESETS = {
    "default": DEFAULT_TRAITS,
    "unhinged": {
        "curiosity": 90,
        "bluntness": 95,
        "warmth": 25,
        "sarcasm": 90,
        "formality": 10,
    },
    "professional": {
        "curiosity": 55,
        "bluntness": 70,
        "warmth": 45,
        "sarcasm": 25,
        "formality": 70,
    },
    "tired friend": {
        "curiosity": 45,
        "bluntness": 75,
        "warmth": 55,
        "sarcasm": 45,
        "formality": 10,
    },
}
PERSONALITY_PRESETS = BUILTIN_PERSONALITY_PRESETS


def _clamp(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return max(0, min(100, number))


@dataclass(frozen=True)
class PersonalityTraits:
    curiosity: int = DEFAULT_TRAITS["curiosity"]
    bluntness: int = DEFAULT_TRAITS["bluntness"]
    warmth: int = DEFAULT_TRAITS["warmth"]
    sarcasm: int = DEFAULT_TRAITS["sarcasm"]
    formality: int = DEFAULT_TRAITS["formality"]

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "PersonalityTraits":
        values = values or {}
        merged = {**DEFAULT_TRAITS, **values}
        return cls(**{key: _clamp(merged[key]) for key in TRAIT_KEYS})

    def to_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in TRAIT_KEYS}

    def updated(self, patch: dict[str, Any]) -> "PersonalityTraits":
        return PersonalityTraits.from_mapping({**self.to_dict(), **(patch or {})})


def load_traits(path: Path = CONFIG_PATH) -> PersonalityTraits:
    if not path.exists():
        return PersonalityTraits.from_mapping(DEFAULT_TRAITS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PersonalityTraits.from_mapping(DEFAULT_TRAITS)
    return PersonalityTraits.from_mapping(data)


def save_traits(traits: PersonalityTraits | dict[str, Any], path: Path = CONFIG_PATH) -> PersonalityTraits:
    traits = traits if isinstance(traits, PersonalityTraits) else PersonalityTraits.from_mapping(traits)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(traits.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return traits


def load_presets(path: Path = PRESETS_PATH) -> dict[str, dict[str, int]]:
    if not path.exists():
        return {
            name: PersonalityTraits.from_mapping(values).to_dict()
            for name, values in BUILTIN_PERSONALITY_PRESETS.items()
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    presets = {
        name: PersonalityTraits.from_mapping(values).to_dict()
        for name, values in BUILTIN_PERSONALITY_PRESETS.items()
    }
    for name, values in (data or {}).items():
        if isinstance(name, str) and isinstance(values, dict):
            presets[name] = PersonalityTraits.from_mapping(values).to_dict()
    return presets


def apply_preset(
    name: str,
    path: Path = CONFIG_PATH,
    presets_path: Path = PRESETS_PATH,
) -> PersonalityTraits:
    presets = load_presets(presets_path)
    if name not in presets:
        raise ValueError(f"unknown personality preset: {name}")
    return save_traits(PersonalityTraits.from_mapping(presets[name]), path=path)
