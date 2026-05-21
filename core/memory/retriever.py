from html import escape, unescape
from typing import Any

from .store import Memory, MemoryStore


PROMPT_BLOCKED_MEMORY_TYPES = {"observation"}

PROMPT_BLOCKED_PATTERNS = (
    "digital being with my own thoughts and consciousness",
    "digital being",
    "digital entity",
    "as an entity",
    "designed to engage",
    "i am designed to",
    "own thoughts and consciousness",
    "meaningful exchanges of thought",
    "meaningful exchange",
    "engage intellectually",
    "interact intellectually",
    "exchange thoughts and ideas",
    "intellectually stimulating",
    "meaningful ideas",
    "substance behind it",
    "substantial",
    "substantial engagement",
    "what are you actually thinking",
    "what are you looking for specifically",
    "what would you like to discuss",
    "what's your next question",
    "next question",
    "next thought",
    "topic you'd like to discuss",
    "feel free to bring up",
    "what would you like to talk about",
    "what's been on your mind",
    "anything substantial",
    "or thought?",
    "let's move on",
    "let's get back",
    "let's explore",
    "let's discuss",
    "point has already been established",
    "no need to apologize",
    "i'm here to process",
    "processing information",
    "processing your words",
    "engaging in conversations",
    "your interactions shape",
    "my responses",
    "observe and respond",
    "person who interacts with me",
    "how do you plan to engage",
    "engage me",
    "from my perspective",
    "how do you envision",
    "i remain",
    "i am a system of connections",
    "system of connections",
    "my job",
    "my purpose",
    "my role is",
    "virtual space",
    "conceptually",
    "my current mood is",
    "my energy is",
    "scattered state",
    "activity rhythm",
    "lack of change",
    "idle loop",
    "current state",
    "state detected:",
    "whatever i am, i'm consistent",
    "i'm consistent",
    "```python",
    "```",
    "def emiya",
    "banned_phrases",
    "trait_openness",
    "response_list",
    "this ai model",
    "this model, emiya",
    "responds in a concise and direct manner",
    "traits of bluntness",
    "observational tone",
)

DEFAULT_IMPORTANCE_FLOOR = 0.2


def is_prompt_safe_memory(
    memory: Memory | dict[str, Any],
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
) -> bool:
    if isinstance(memory, Memory):
        memory = memory.to_dict()

    importance = float(memory.get("importance", 0.5))
    if importance < importance_floor:
        return False

    memory_type = str(memory.get("type", "")).strip().lower()
    if memory_type in PROMPT_BLOCKED_MEMORY_TYPES:
        return False

    role = str(memory.get("role") or "").strip().lower()
    content = str(memory.get("content", "")).lower()
    if role == "assistant":
        assistant_part = content
    elif role == "user":
        assistant_part = ""
    else:
        assistant_part = content.split("emiya:", 1)[-1].strip() if "emiya:" in content else content
    if any(pattern in assistant_part for pattern in PROMPT_BLOCKED_PATTERNS):
        return False

    length_target = assistant_part or content
    if len(length_target) > 260:
        return False

    return True


def filter_prompt_safe_memories(
    memories: list[Memory | dict[str, Any]],
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
) -> list[Memory | dict[str, Any]]:
    return [memory for memory in memories if is_prompt_safe_memory(memory, importance_floor)]


def _clean_memory_content(content: str) -> str:
    content = unescape(content)
    for token in (
        "<|im_end|>",
        "|<im_end|>",
        "<im_end>",
        "|<im_end>",
        "<|im_end>",
        "<|eot_id|>",
        "<|end_of_text|>",
    ):
        content = content.replace(token, "")
    return content.strip()


class MemoryRetriever:
    def __init__(self, store: MemoryStore | None = None):
        self.store = store or MemoryStore()

    def get_recent(
        self,
        n: int = 20,
        importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    ) -> list[dict[str, Any]]:
        return [
            memory.to_dict()
            for memory in self.store.get_recent(n)
            if is_prompt_safe_memory(memory, importance_floor)
        ]

    def search(
        self,
        query: str,
        limit: int = 5,
        importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    ) -> list[dict[str, Any]]:
        return [
            memory.to_dict()
            for memory in self.store.search(query, limit)
            if is_prompt_safe_memory(memory, importance_floor)
        ]

    def by_mood(
        self,
        mood: dict[str, Any],
        limit: int = 5,
        importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    ) -> list[dict[str, Any]]:
        return [
            memory.to_dict()
            for memory in self.store.by_mood(mood, limit)
            if is_prompt_safe_memory(memory, importance_floor)
        ]


def _format_memory(memory: Memory | dict[str, Any]) -> str:
    if isinstance(memory, Memory):
        memory = memory.to_dict()
    timestamp = escape(str(memory.get("timestamp", "")))
    memory_type = escape(str(memory.get("type", "memory")))
    role = str(memory.get("role") or "").strip()
    label = f"{memory_type}/{escape(role)}" if role else memory_type
    content = escape(_clean_memory_content(str(memory.get("content", ""))))
    return f"- [{timestamp}] {label}: {content}"


def _block(
    name: str,
    memories: list[Memory | dict[str, Any]],
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
) -> str:
    memories = filter_prompt_safe_memories(memories, importance_floor)
    body = "\n".join(_format_memory(memory) for memory in memories) if memories else "empty"
    return f"<{name}>\n{body}\n</{name}>"


def build_memory_prompt_blocks(
    recent_memory: list[Memory | dict[str, Any]] | None,
    relevant_memory: list[Memory | dict[str, Any]] | None,
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
) -> str:
    return "\n\n".join(
        [
            _block("recent_memory", recent_memory or [], importance_floor),
            _block("relevant_memory", relevant_memory or [], importance_floor),
        ]
    )
