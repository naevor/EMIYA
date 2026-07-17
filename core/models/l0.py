from pathlib import Path

import requests

from models.response_utils import (
    split_thinking,
    strip_generation_artifacts,
    strip_speaker_prefix,
    take_sentence_prefix,
)


MODEL = "qwen3:4b-instruct-2507-q4_K_M"
OLLAMA_URL = "http://localhost:11434/api/chat"

_prompt_file = Path(__file__).parent.parent / "prompts" / "l0.txt"
SYSTEM_PROMPT = _prompt_file.read_text(encoding="utf-8")
BASE_OPTIONS = {
    "temperature": 0.85,
    "top_p": 0.9,
    "num_predict": 100,
    "num_ctx": 4096,
}


def _build_system(mood: dict | None, traits: dict | None = None) -> str:
    blocks = []

    if mood:
        try:
            from mood.modifiers import mood_from_mapping, mood_to_prompt_fragment

            blocks.append(mood_to_prompt_fragment(mood_from_mapping(mood)))
        except Exception as e:
            print(f"[L0] mood injection error: {e}")

    try:
        from personality.modifiers import traits_to_prompt_fragment
        from personality.traits import load_traits

        traits = traits or load_traits().to_dict()
        blocks.append(traits_to_prompt_fragment(traits))
    except Exception as e:
        print(f"[L0] traits injection error: {e}")

    blocks.append(SYSTEM_PROMPT)
    blocks.append(
        """
<instruction>
respond only in english.
one or two short sentences.
do not mention system labels, mood, traits, or internal state names.
</instruction>
""".strip()
    )
    return "\n\n".join(blocks)


def _build_options(mood: dict | None) -> dict:
    options = dict(BASE_OPTIONS)

    try:
        from mood.modifiers import mood_from_mapping, mood_to_model_options

        return mood_to_model_options(mood_from_mapping(mood), options)
    except Exception as e:
        print(f"[L0] mood options error: {e}")
        return options


def build_user_prompt(trigger: str, context: dict) -> str:
    active_min = context.get("active_min", 0)
    apps = context.get("apps", [])
    hour = context.get("hour", 0)
    top_app = apps[0]["app"].replace(".exe", "") if apps else "unknown"

    situations = {
        "grinding": f"the user has been working for {int(active_min)} minutes without a break. app: {top_app}.",
        "late_night_grinding": f"it is {hour}:00 at night. the user has been working for {int(active_min)} minutes. app: {top_app}.",
        "scattered": f"the user has been switching between apps chaotically for {int(active_min)} minutes.",
        "idle_loop": "the user is circling through the same windows.",
        "late_night": f"it is {hour}:00 at night. the user is still at the computer.",
        "afk_return": "the user returned after a break.",
        "first_start": "the user just started the session.",
    }

    situation = situations.get(trigger, "you are observing the user.")
    return f"situation: {situation}\nsay something. one or two sentences, no more. /no_think"


def _clean(text: str) -> str:
    text = strip_generation_artifacts(text)
    text = strip_speaker_prefix(text)
    text = strip_generation_artifacts(text)
    text = text.replace("!", ".")
    return take_sentence_prefix(text, max_sentences=2)


def generate(trigger: str, context: dict, return_metadata: bool = False) -> str | dict | None:
    mood = context.get("mood")
    traits = context.get("traits")
    system = _build_system(mood, traits)
    options = _build_options(mood)

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": build_user_prompt(trigger, context)},
                ],
                "stream": False,
                "think": False,
                "options": options,
                "keep_alive": "2m",
            },
            timeout=(5, 45),
        )

        if response.status_code == 200:
            raw_text = response.json().get("message", {}).get("content", "").strip()
            visible_text, thought = split_thinking(raw_text)
            cleaned = _clean(visible_text)
            if return_metadata:
                return {
                    "content": cleaned,
                    "thought": thought,
                    "raw_response": raw_text,
                    "model": MODEL,
                    "mood_seed": options.get("seed"),
                    "system_prompt": system,
                }
            return cleaned
        return None

    except Exception as e:
        print(f"[L0] error: {e}")
        return None


if __name__ == "__main__":
    ctx = {
        "active_min": 0,
        "apps": [],
        "hour": 10,
        "mood": {"energy": 0.5, "focus": 0.5, "openness": 0.5},
    }
    print(generate("first_start", ctx))
