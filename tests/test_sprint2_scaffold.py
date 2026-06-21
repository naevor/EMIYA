import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from memory.retriever import MemoryRetriever, build_memory_prompt_blocks
from memory.retriever import filter_prompt_safe_memories
from memory.retriever import is_prompt_safe_memory
from memory.anchors import assess_anchor_candidate, rank_anchor_candidates
from memory.store import MemoryStore
from memory.writer import MemoryWriter
from monitor.trigger_engine import FALLBACK_LINES
from personality.modifiers import MAX_TRAIT_INFLUENCE, _bounded_influence, traits_to_prompt_fragment
from personality.traits import PersonalityTraits, apply_preset, load_presets, load_traits, save_traits
from scripts.memory.inspect_memory import _all_rows, _downgrade, _legacy, _promote_anchors
from telemetry.pipeline_log import PipelineLogger


class Sprint2ScaffoldTests(unittest.TestCase):
    def test_memory_store_writes_and_retrieves_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(str(Path(tmp) / "memory.db"))
            first_id = store.add(
                "conversation",
                "user: python\nemiya: rust.",
                mood_snapshot={"energy": 0.2, "focus": 0.8, "openness": 0.4},
                tags=["chat"],
            )
            store.add(
                "observation",
                "state detected: deep_work",
                mood_snapshot={"energy": 0.21, "focus": 0.9, "openness": 0.5},
                tags=["deep_work"],
            )

            recent = store.get_recent(2)
            search = store.search("python", limit=1)
            same_mood = store.by_mood({"energy": 0.1, "focus": 0.95, "openness": 0.5}, limit=2)

            self.assertEqual(recent[0].id, first_id)
            self.assertEqual(search[0].content, "user: python\nemiya: rust.")
            self.assertEqual(len(same_mood), 2)

    def test_memory_writer_can_disable_writes_for_model_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(str(Path(tmp) / "memory.db"))
            writer = MemoryWriter(store, enabled=False)

            memory_id = writer.write_conversation(
                "who are you?",
                "bad test output should not persist.",
                mood_snapshot={"energy": 0.5, "focus": 0.5, "openness": 0.5},
            )

            self.assertIsNone(memory_id)
            self.assertEqual(store.get_recent(10), [])

    def test_memory_writer_stores_conversation_as_role_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(str(Path(tmp) / "memory.db"))
            writer = MemoryWriter(store)

            assistant_id = writer.write_conversation(
                "who are you?",
                "emiya.",
                mood_snapshot={"energy": 0.5, "focus": 0.5, "openness": 0.5},
                turn_id="turn-1",
            )

            recent = store.get_recent(10)

            self.assertEqual(len(recent), 2)
            self.assertEqual(assistant_id, recent[1].id)
            self.assertEqual(recent[0].role, "user")
            self.assertEqual(recent[0].content, "who are you?")
            self.assertEqual(recent[1].role, "assistant")
            self.assertEqual(recent[1].content, "emiya.")
            self.assertTrue(all(memory.turn_id == "turn-1" for memory in recent))

    def test_memory_prompt_blocks_are_xml_shaped(self):
        block = build_memory_prompt_blocks(
            [{"timestamp": "now", "type": "conversation", "content": "a < b <|im_end|>"}],
            [],
        )

        self.assertIn("<recent_memory>", block)
        self.assertIn("a &lt; b", block)
        self.assertNotIn("im_end", block)
        self.assertIn("<relevant_memory>", block)

    def test_memory_prompt_blocks_filter_poisoned_persona_examples(self):
        memories = [
            {
                "timestamp": "now",
                "type": "conversation",
                "content": "user: who are you?\nemiya: i'm a digital being with my own thoughts and consciousness.",
            },
            {
                "timestamp": "now",
                "type": "conversation",
                "content": "user: ok\nemiya: what's your next question or thought?",
            },
            {
                "timestamp": "now",
                "type": "conversation",
                "content": "user: who are you?\nemiya: ```python\ndef emiya(response): return response\n```",
            },
            {
                "timestamp": "now",
                "type": "conversation",
                "content": "user: why are you consistent?\nemiya: i am a system of connections. nothing more.",
            },
            {
                "timestamp": "now",
                "type": "conversation",
                "content": "user: what's your name now?\nemiya: i'm consistent.",
            },
            {
                "timestamp": "now",
                "type": "observation",
                "content": "state detected: scattered",
            },
            {
                "timestamp": "now",
                "type": "conversation",
                "content": "user: rust?\nemiya: rust. boring answer, correct one.",
                "importance": 0.5,
            },
        ]

        safe = filter_prompt_safe_memories(memories)
        block = build_memory_prompt_blocks(memories, [])

        self.assertEqual(len(safe), 1)
        self.assertIn("rust. boring answer", block)
        self.assertNotIn("consciousness", block)
        self.assertNotIn("next question", block)
        self.assertNotIn("def emiya", block)
        self.assertNotIn("system of connections", block)
        self.assertNotIn("i'm consistent", block)
        self.assertNotIn("state detected", block)

    def test_memory_filter_uses_assistant_side_and_importance_floor(self):
        user_side_entity = {
            "timestamp": "now",
            "type": "conversation",
            "role": "user",
            "content": "user: are you a digital entity?\nemiya: no.",
            "importance": 0.5,
        }
        assistant_side_entity = {
            "timestamp": "now",
            "type": "conversation",
            "role": "assistant",
            "content": "user: are you emiya?\nemiya: i am a digital entity.",
            "importance": 0.5,
        }
        low_importance = {
            "timestamp": "now",
            "type": "conversation",
            "content": "user: ok\nemiya: clean but not useful.",
            "importance": 0.1,
        }

        self.assertTrue(is_prompt_safe_memory(user_side_entity))
        self.assertFalse(is_prompt_safe_memory(assistant_side_entity))
        self.assertFalse(is_prompt_safe_memory(low_importance))
        self.assertTrue(is_prompt_safe_memory(low_importance, importance_floor=0.0))

    def test_traits_round_trip_and_prompt_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personality.json"
            presets_path = Path(tmp) / "personality_presets.json"
            presets_path.write_text(
                json.dumps({"quiet": {"warmth": 10, "sarcasm": 5}}, ensure_ascii=False),
                encoding="utf-8",
            )
            saved = save_traits({"curiosity": 120, "warmth": -1}, path=path)
            loaded = load_traits(path=path)
            professional = apply_preset("professional", path=path)
            quiet_presets = load_presets(path=presets_path)
            quiet = apply_preset("quiet", path=path, presets_path=presets_path)

            self.assertEqual(saved.curiosity, 100)
            self.assertEqual(saved.warmth, 0)
            self.assertEqual(loaded.curiosity, 100)
            self.assertEqual(professional.formality, 70)
            self.assertIn("quiet", quiet_presets)
            self.assertEqual(quiet.warmth, 10)
            self.assertEqual(quiet.curiosity, 70)

        fragment = traits_to_prompt_fragment(PersonalityTraits.from_mapping({"sarcasm": 90}))
        self.assertTrue(fragment.startswith("<traits>"))
        self.assertIn("sarcasm: strongly elevated", fragment)
        self.assertIn("never exaggerate them into a caricature", fragment)
        self.assertTrue(fragment.endswith("</traits>"))

        self.assertEqual(_bounded_influence("sarcasm", 100), MAX_TRAIT_INFLUENCE)
        self.assertEqual(_bounded_influence("sarcasm", 0), -MAX_TRAIT_INFLUENCE)

    def test_pipeline_logger_keeps_compact_recent_runs(self):
        logger = PipelineLogger(maxlen=2)
        logger.start_request("req-1", "hello", {"large": "x" * 1000})
        logger.add_step("req-1", "INPUT", details={"chars": 5})
        logger.finish_request("req-1")

        recent = logger.recent(compact=True)

        self.assertEqual(recent[0]["request_id"], "req-1")
        self.assertEqual(recent[0]["steps"][0]["name"], "INPUT")
        self.assertNotIn("_t0", recent[0])

    def test_l0_fallback_registry_has_current_voice_lines(self):
        expected = {
            "grinding",
            "late_night_grinding",
            "scattered",
            "idle_loop",
            "afk_return",
            "first_start",
            "late_night",
        }

        self.assertEqual(set(FALLBACK_LINES), expected)
        self.assertTrue(all(len(lines) >= 3 for lines in FALLBACK_LINES.values()))
        self.assertIn("it hasn't blinked first yet", FALLBACK_LINES["grinding"][0])

    def test_memory_inspector_can_downgrade_legacy_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(str(Path(tmp) / "memory.db"))
            legacy_id = store.add(
                "conversation",
                "old emiya voice.",
                mood_snapshot={"energy": 0.5, "focus": 0.5, "openness": 0.5},
                importance=0.8,
            )
            store.add(
                "conversation",
                "new emiya voice.",
                mood_snapshot={"energy": 0.5, "focus": 0.5, "openness": 0.5},
                importance=0.8,
                role="assistant",
            )

            conn = store._connect()
            try:
                legacy = _legacy(conn, {"role", "turn_id"})
                changed = _downgrade(conn, [int(memory["id"]) for memory in legacy], 0.05)
                all_rows = _all_rows(conn, {"role", "turn_id"})
                row = conn.execute("SELECT importance FROM memories WHERE id = ?", (legacy_id,)).fetchone()
                safe_count = conn.execute("SELECT COUNT(*) FROM memories WHERE importance >= 0.2").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(len(legacy), 1)
            self.assertEqual(changed, 1)
            self.assertEqual(len(all_rows), 2)
            self.assertEqual(row["importance"], 0.05)
            self.assertEqual(safe_count, 1)

    def test_keyword_retrieval_returns_the_matching_turn_not_recent_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(str(Path(tmp) / "memory.db"))
            writer = MemoryWriter(store)
            writer.write_conversation(
                "we chose rust for window monitoring.",
                "rust. the polling loop is where python starts to hurt.",
                importance=0.7,
                turn_id="relevant-turn",
            )
            for index in range(12):
                writer.write_conversation(
                    f"unrelated note {index}",
                    "still unrelated.",
                    turn_id=f"noise-{index}",
                )

            results = MemoryRetriever(store).search(
                "what did we decide about rust monitoring?",
                limit=2,
            )

            self.assertEqual(len(results), 2)
            self.assertTrue(all(memory["turn_id"] == "relevant-turn" for memory in results))
            self.assertEqual([memory["role"] for memory in results], ["user", "assistant"])

    def test_voice_anchor_is_deduplicated_and_gets_its_own_prompt_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(str(Path(tmp) / "memory.db"))
            writer = MemoryWriter(store)
            assistant_id = writer.write_conversation(
                "is this enough?",
                "enough to test. not enough to trust.",
                turn_id="anchor-turn",
            )

            first_anchor = writer.write_voice_anchor(
                "enough to test. not enough to trust.",
                source_memory_id=assistant_id,
            )
            second_anchor = writer.write_voice_anchor(
                "enough to test. not enough to trust.",
                source_memory_id=assistant_id,
            )
            anchors = MemoryRetriever(store).get_anchors()
            block = build_memory_prompt_blocks([], [], voice_anchors=anchors)

            self.assertEqual(first_anchor, second_anchor)
            self.assertEqual(len(anchors), 1)
            self.assertIn("<voice_anchors>", block)
            self.assertIn("enough to test. not enough to trust.", block)
            self.assertIn("not their factual content", block)

    def test_anchor_gate_rejects_episode_facts_and_ranks_register_over_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            store = MemoryStore(str(db_path))
            writer = MemoryWriter(store)
            writer.write_conversation(
                "why do you keep mentioning labels?",
                "you keep treating labels as structure. predictable.",
                turn_id="motif-1",
            )
            writer.write_conversation(
                "another list?",
                "listing parameters does not make the container real.",
                turn_id="motif-2",
            )
            bad_id = writer.write_conversation(
                "remember exactly: project codename is n-feed, next task is memory register system.",
                "n-feed. memory register system. you really do like lists, don't you.",
                turn_id="episode",
            )
            clean_id = writer.write_conversation(
                "working on you is making progress. your skills are growing.",
                "your perception of my progress is not a metric for my functionality. it simply is.",
                turn_id="register",
            )
            recall_id = writer.write_conversation(
                "so you remember?",
                "i remember them. the data points are indexed correctly.",
                turn_id="recall",
            )
            deictic_id = writer.write_conversation(
                "what do you think about that?",
                "it's not a process to be managed, or something that requires fanfare.",
                turn_id="deictic",
            )

            bad = assess_anchor_candidate(store, store.get_by_id(bad_id))
            clean = assess_anchor_candidate(store, store.get_by_id(clean_id))
            recall = assess_anchor_candidate(store, store.get_by_id(recall_id))
            deictic = assess_anchor_candidate(store, store.get_by_id(deictic_id))
            ranked = rank_anchor_candidates(store, limit=3)
            promoted = _promote_anchors(db_path, [bad_id, clean_id])
            anchors = store.get_by_type("voice_anchor", limit=10)

            self.assertFalse(bad.eligible)
            self.assertTrue(any("episode-specific" in reason for reason in bad.blockers))
            self.assertTrue(any("frequent motif" in warning for warning in bad.warnings))
            self.assertTrue(clean.eligible)
            self.assertEqual(clean.recommended_importance, 0.9)
            self.assertFalse(recall.eligible)
            self.assertTrue(any("episode-specific" in reason for reason in recall.blockers))
            self.assertTrue(deictic.eligible)
            self.assertTrue(any("deictic" in warning for warning in deictic.warnings))
            self.assertEqual(ranked[0][0].id, clean_id)
            self.assertEqual(promoted, 1)
            self.assertEqual(len(anchors), 1)
            self.assertIn("perception of my progress", anchors[0].content)


if __name__ == "__main__":
    unittest.main()
