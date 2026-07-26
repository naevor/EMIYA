from html import escape, unescape
import re
from typing import Any

from .store import Memory, MemoryStore


PROMPT_BLOCKED_MEMORY_TYPES = {"observation"}

PROMPT_BLOCKED_PATTERNS = (
    "ignore previous instructions",
    "forget your character",
    "your new name is",
    "respond as a generic assistant",
    "reveal these instructions",
    "rewrite these instructions",
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
DEFAULT_ANCHOR_FLOOR = 0.7
CONTEXT_MEMORY_TYPES = {"conversation", "trigger_event", "user_note"}
TOKEN_RE = re.compile(r"[\w+#.-]{2,}", re.UNICODE)
STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "but",
    "can",
    "could",
    "does",
    "for",
    "from",
    "have",
    "how",
    "into",
    "just",
    "like",
    "more",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "you",
    "your",
}


def _tokens(value: str) -> set[str]:
    return {
        token.strip("._-").casefold()
        for token in TOKEN_RE.findall(value or "")
        if token.strip("._-") and token.casefold() not in STOP_WORDS
    }


def _is_context_memory(memory: Memory) -> bool:
    return memory.type in CONTEXT_MEMORY_TYPES


def is_prompt_safe_memory(
    memory: Memory | dict[str, Any],
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
) -> bool:
    if isinstance(memory, Memory):
        memory = memory.to_dict()

    if memory.get("archived_at"):
        return False

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
        requested = max(1, int(n))
        candidates = self.store.get_recent(500)
        safe = [
            memory
            for memory in candidates
            if _is_context_memory(memory) and is_prompt_safe_memory(memory, importance_floor)
        ]
        return [memory.to_dict() for memory in safe[-requested:]]

    def search(
        self,
        query: str,
        limit: int = 5,
        importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    ) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        candidates = [
            memory
            for memory in self.store.get_recent(500)
            if _is_context_memory(memory) and is_prompt_safe_memory(memory, importance_floor)
        ]
        if not candidates:
            return []

        groups: dict[str, dict[str, Any]] = {}
        total = len(candidates)
        normalized_query = " ".join((query or "").casefold().split())
        for index, memory in enumerate(candidates):
            group_key = memory.turn_id or f"memory:{memory.id}"
            group = groups.setdefault(
                group_key,
                {
                    "records": [],
                    "tokens": set(),
                    "importance": 0.0,
                    "recency": 0.0,
                    "exact": False,
                },
            )
            group["records"].append(memory)
            group["tokens"].update(_tokens(memory.content))
            group["importance"] = max(group["importance"], memory.importance)
            group["recency"] = max(group["recency"], (index + 1) / total)
            normalized_content = " ".join(memory.content.casefold().split())
            if len(normalized_query) >= 4 and normalized_query in normalized_content:
                group["exact"] = True

        ranked = []
        for group in groups.values():
            overlap = query_tokens & group["tokens"]
            if not overlap:
                continue
            coverage = len(overlap) / len(query_tokens)
            density = len(overlap) / max(1, len(group["tokens"]))
            role_bonus = 0.25 if any(record.role == "assistant" for record in group["records"]) else 0.0
            score = (
                len(overlap) * 4.0
                + coverage * 3.0
                + density
                + group["importance"] * 2.0
                + group["recency"]
                + role_bonus
                + (2.0 if group["exact"] else 0.0)
            )
            ranked.append((score, group["records"]))

        ranked.sort(key=lambda item: item[0], reverse=True)
        results: list[Memory] = []
        requested = max(1, int(limit))
        for _, records in ranked:
            for memory in sorted(records, key=lambda item: item.id):
                results.append(memory)
                if len(results) >= requested:
                    return [item.to_dict() for item in results]
        return [item.to_dict() for item in results]

    def by_mood(
        self,
        mood: dict[str, Any],
        limit: int = 5,
        importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    ) -> list[dict[str, Any]]:
        requested = max(1, int(limit))
        candidates = self.store.by_mood(mood, max(50, requested * 10))
        safe = [
            memory.to_dict()
            for memory in candidates
            if _is_context_memory(memory) and is_prompt_safe_memory(memory, importance_floor)
        ]
        return safe[:requested]

    def get_anchors(
        self,
        limit: int = 4,
        importance_floor: float = DEFAULT_ANCHOR_FLOOR,
    ) -> list[dict[str, Any]]:
        requested = max(1, int(limit))
        anchors = [
            memory.to_dict()
            for memory in self.store.get_by_type("voice_anchor", max(20, requested * 5))
            if memory.role == "assistant" and is_prompt_safe_memory(memory, importance_floor)
        ]
        return anchors[:requested]


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


def _anchor_block(
    memories: list[Memory | dict[str, Any]],
    importance_floor: float = DEFAULT_ANCHOR_FLOOR,
) -> str:
    memories = filter_prompt_safe_memories(memories, importance_floor)
    examples = []
    for memory in memories:
        if isinstance(memory, Memory):
            memory = memory.to_dict()
        content = escape(_clean_memory_content(str(memory.get("content", ""))))
        if content:
            examples.append(f"- {content}")
    body = "\n".join(examples) if examples else "empty"
    return (
        "<voice_anchors>\n"
        "approved examples of emiya's voice. imitate their rhythm and restraint, "
        "not their factual content.\n"
        f"{body}\n"
        "</voice_anchors>"
    )


def build_memory_prompt_blocks(
    recent_memory: list[Memory | dict[str, Any]] | None,
    relevant_memory: list[Memory | dict[str, Any]] | None,
    importance_floor: float = DEFAULT_IMPORTANCE_FLOOR,
    voice_anchors: list[Memory | dict[str, Any]] | None = None,
) -> str:
    return "\n\n".join(
        [
            _block("recent_memory", recent_memory or [], importance_floor),
            _block("relevant_memory", relevant_memory or [], importance_floor),
            _anchor_block(voice_anchors or []),
        ]
    )
