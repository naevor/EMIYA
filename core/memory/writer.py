from typing import Any

from .store import MemoryStore


class MemoryWriter:
    def __init__(self, store: MemoryStore | None = None, enabled: bool = True):
        self.store = store or MemoryStore()
        self.enabled = bool(enabled)

    def write_conversation(
        self,
        user_text: str,
        assistant_text: str,
        mood_snapshot: dict[str, Any] | None = None,
        importance: float = 0.5,
        tags: list[str] | None = None,
        turn_id: str | None = None,
    ) -> int | None:
        if not self.enabled:
            return None
        common_tags = ["chat", *(tags or [])]
        self.store.add(
            "conversation",
            user_text.strip(),
            mood_snapshot=mood_snapshot,
            importance=min(importance, 0.45),
            tags=[*common_tags, "user"],
            role="user",
            turn_id=turn_id,
        )
        return self.store.add(
            "conversation",
            assistant_text.strip(),
            mood_snapshot=mood_snapshot,
            importance=importance,
            tags=[*common_tags, "assistant"],
            role="assistant",
            turn_id=turn_id,
        )

    def write_observation(
        self,
        content: str,
        mood_snapshot: dict[str, Any] | None = None,
        importance: float = 0.35,
        tags: list[str] | None = None,
    ) -> int | None:
        if not self.enabled:
            return None
        return self.store.add(
            "observation",
            content,
            mood_snapshot=mood_snapshot,
            importance=importance,
            tags=["monitor", *(tags or [])],
            role="system",
        )

    def write_trigger_event(
        self,
        trigger: str,
        message: str,
        mood_snapshot: dict[str, Any] | None = None,
        importance: float = 0.6,
    ) -> int | None:
        if not self.enabled:
            return None
        content = f"trigger: {trigger.strip()}\nemiya: {message.strip()}"
        return self.store.add(
            "trigger_event",
            content,
            mood_snapshot=mood_snapshot,
            importance=importance,
            tags=["l0", trigger],
            role="assistant",
        )

    def write_voice_anchor(
        self,
        content: str,
        mood_snapshot: dict[str, Any] | None = None,
        importance: float = 0.9,
        tags: list[str] | None = None,
        source_memory_id: int | None = None,
    ) -> int | None:
        if not self.enabled:
            return None

        source_tag = f"source:{int(source_memory_id)}" if source_memory_id is not None else None
        clean_content = content.strip()
        for anchor in self.store.get_by_type("voice_anchor", limit=500):
            if anchor.content.strip() != clean_content:
                continue
            if source_tag is None or source_tag in anchor.tags:
                return anchor.id

        anchor_tags = ["voice_anchor", *(tags or [])]
        if source_tag:
            anchor_tags.append(source_tag)
        return self.store.add(
            "voice_anchor",
            clean_content,
            mood_snapshot=mood_snapshot,
            importance=max(0.7, importance),
            tags=anchor_tags,
            role="assistant",
        )
