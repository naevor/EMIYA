import re
from collections import Counter
from dataclasses import dataclass

from .retriever import is_prompt_safe_memory
from .store import Memory, MemoryStore


ANCHOR_MIN_IMPORTANCE = 0.2
FREQUENT_MOTIF_MIN_COUNT = 2
TOKEN_RE = re.compile(r"[\w+#.-]{2,}", re.UNICODE)
STOP_WORDS = {
    "as",
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "at",
    "be",
    "been",
    "being",
    "but",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "have",
    "how",
    "in",
    "into",
    "is",
    "it",
    "just",
    "like",
    "me",
    "more",
    "my",
    "no",
    "not",
    "of",
    "on",
    "our",
    "so",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
    "you",
    "your",
}
PROJECT_FACT_PATTERNS = (
    re.compile(r"\b(?:project|codename|repository|repo|next task|version)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:i remember|remember them|data points?|indexed|nickname|age|designated|called|named|english)\b",
        re.IGNORECASE,
    ),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"`[^`]+`"),
    re.compile(r"\b[\w-]+\.(?:py|js|jsx|ts|tsx|json|md|txt|toml|yaml|yml)\b", re.IGNORECASE),
    re.compile(r"\b[a-z][a-z0-9]*-[a-z0-9-]+\b", re.IGNORECASE),
)
CONTEXT_NEUTRAL_OVERLAP = {
    "answer",
    "functionality",
    "idea",
    "perception",
    "problem",
    "process",
    "progress",
    "question",
    "thought",
    "work",
}
DEICTIC_OPENING = re.compile(r"^(?:it|it's|this|that|these|those)\b", re.IGNORECASE)
MOTIF_FAMILIES = {
    "labels_and_structure": {
        "categorize",
        "category",
        "container",
        "definitive",
        "label",
        "labels",
        "list",
        "listing",
        "lists",
        "metadata",
        "parameter",
        "parameters",
        "structure",
    },
    "avoidance_loop": {
        "avoid",
        "avoiding",
        "circle",
        "circling",
        "deciding",
        "escape",
        "loop",
        "running",
        "stuck",
    },
}


@dataclass(frozen=True)
class AnchorAssessment:
    memory_id: int
    eligible: bool
    score: float
    recommended_importance: float
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    motifs: tuple[str, ...]


def _tokens(value: str) -> set[str]:
    return {
        token.strip("._-").casefold()
        for token in TOKEN_RE.findall(value or "")
        if token.strip("._-") and token.casefold() not in STOP_WORDS
    }


def _motifs(value: str) -> set[str]:
    tokens = _tokens(value)
    return {
        name
        for name, vocabulary in MOTIF_FAMILIES.items()
        if tokens & vocabulary
    }


def _paired_user(store: MemoryStore, memory: Memory) -> Memory | None:
    if not memory.turn_id:
        return None
    for candidate in store.get_recent(500):
        if candidate.turn_id == memory.turn_id and candidate.role == "user":
            return candidate
    return None


def _active_assistant_memories(store: MemoryStore, limit: int = 200) -> list[Memory]:
    return [
        memory
        for memory in store.get_recent(max(1, int(limit)))
        if memory.type == "conversation"
        and memory.role == "assistant"
        and memory.importance >= ANCHOR_MIN_IMPORTANCE
    ]


def _motif_counts(store: MemoryStore) -> Counter[str]:
    counts: Counter[str] = Counter()
    for memory in _active_assistant_memories(store):
        counts.update(_motifs(memory.content))
    return counts


def _anchor_similarity(store: MemoryStore, candidate_tokens: set[str]) -> float:
    highest = 0.0
    for anchor in store.get_by_type("voice_anchor", limit=500):
        anchor_tokens = _tokens(anchor.content)
        union = candidate_tokens | anchor_tokens
        if union:
            highest = max(highest, len(candidate_tokens & anchor_tokens) / len(union))
    return highest


def assess_anchor_candidate(store: MemoryStore, memory: Memory) -> AnchorAssessment:
    blockers: list[str] = []
    warnings: list[str] = []
    content = memory.content.strip()
    candidate_tokens = _tokens(content)

    if memory.type != "conversation" or memory.role != "assistant":
        blockers.append("not an assistant conversation")
    if memory.importance < ANCHOR_MIN_IMPORTANCE:
        blockers.append("memory is archived or below the active importance floor")
    if not is_prompt_safe_memory(memory, importance_floor=0.0):
        blockers.append("reply is not prompt-safe")
    if len(content) < 24:
        blockers.append("too short to represent a stable voice register")
    if content.count('"') % 2:
        blockers.append("contains an unbalanced quote artifact")
    if any(pattern.search(content) for pattern in PROJECT_FACT_PATTERNS):
        blockers.append("contains episode-specific project facts or identifiers")

    paired_user = _paired_user(store, memory)
    if paired_user:
        overlap = (candidate_tokens & _tokens(paired_user.content)) - CONTEXT_NEUTRAL_OVERLAP
        overlap_ratio = len(overlap) / max(1, len(candidate_tokens))
        if len(overlap) >= 2 and overlap_ratio >= 0.2:
            blockers.append(
                "repeats episode content from the paired user message: "
                + ", ".join(sorted(overlap))
            )
        elif overlap:
            warnings.append("shares one contextual term with the paired user message")
    else:
        warnings.append("paired user message is unavailable")
    if DEICTIC_OPENING.search(content):
        warnings.append("opens with a context-dependent deictic reference")

    motifs = _motifs(content)
    motif_counts = _motif_counts(store)
    frequent_motifs = sorted(
        motif
        for motif in motifs
        if motif_counts[motif] >= FREQUENT_MOTIF_MIN_COUNT
    )
    if frequent_motifs:
        warnings.append("uses an already frequent motif: " + ", ".join(frequent_motifs))

    existing_anchor_motifs = set()
    for anchor in store.get_by_type("voice_anchor", limit=500):
        existing_anchor_motifs.update(_motifs(anchor.content))
    repeated_anchor_motifs = sorted(motifs & existing_anchor_motifs)
    if repeated_anchor_motifs:
        blockers.append("duplicates an anchor motif: " + ", ".join(repeated_anchor_motifs))

    similarity = _anchor_similarity(store, candidate_tokens)
    if similarity >= 0.45:
        blockers.append(f"too similar to an existing anchor ({similarity:.2f})")

    score = 1.0
    if warnings:
        score -= 0.12 * len(warnings)
    if frequent_motifs:
        score -= 0.2
    if len(content) > 180:
        score -= 0.12
    if "?" in content:
        score -= 0.05
    if blockers:
        score = 0.0
    score = round(max(0.0, min(1.0, score)), 3)

    if blockers:
        recommended_importance = 0.0
    elif frequent_motifs:
        recommended_importance = 0.72
    elif warnings:
        recommended_importance = 0.82
    else:
        recommended_importance = 0.9

    return AnchorAssessment(
        memory_id=memory.id,
        eligible=not blockers,
        score=score,
        recommended_importance=recommended_importance,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        motifs=tuple(sorted(motifs)),
    )


def rank_anchor_candidates(
    store: MemoryStore,
    limit: int = 10,
    scan_limit: int = 200,
) -> list[tuple[Memory, AnchorAssessment]]:
    ranked = []
    for memory in reversed(_active_assistant_memories(store, scan_limit)):
        assessment = assess_anchor_candidate(store, memory)
        if assessment.eligible:
            ranked.append((memory, assessment))
    ranked.sort(key=lambda item: (item[1].score, item[0].id), reverse=True)
    return ranked[: max(1, int(limit))]
