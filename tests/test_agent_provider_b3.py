import asyncio
import json
import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from agent.provider import DecisionRequest, FinalResult, InvalidDecision, ToolCall
from models.agent_provider import ENVELOPE_SCHEMA, OllamaAgentProvider


def request():
    return DecisionRequest(task="read x", tools=(), steps=())


def response(envelope):
    return {"message": {"content": json.dumps(envelope)}}


class OllamaAgentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_tool_call_and_sends_structured_payload(self):
        calls = []

        def transport(url, payload, timeout_s):
            calls.append((url, payload, timeout_s))
            return response({"type": "tool_call", "skill": "fs.read", "args": {"path": "x"}})

        provider = OllamaAgentProvider("http://host/", "agent-model", transport=transport)
        decision = await provider.decide(request())

        self.assertEqual(decision, ToolCall("fs.read", {"path": "x"}))
        url, payload, timeout_s = calls[0]
        self.assertEqual(url, "http://host/api/chat")
        self.assertEqual(timeout_s, 120.0)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], ENVELOPE_SCHEMA)
        self.assertEqual(payload["options"]["temperature"], 0.1)
        self.assertNotIn("keep_alive", payload)

    async def test_maps_final_result(self):
        provider = OllamaAgentProvider(
            "http://host",
            "model",
            transport=lambda *_: response({"type": "final", "facts": "grounded"}),
        )
        self.assertEqual(await provider.decide(request()), FinalResult("grounded"))

    async def test_normalizes_invalid_envelopes(self):
        payloads = [
            response({"type": "tool_call", "args": {}}),
            response({"type": "final"}),
            {"message": {"content": "not json " + "x" * 700}},
            {"message": None},
        ]
        expected = [
            "tool_call missing skill",
            "final missing facts",
            "response is not valid JSON",
            "response is not valid JSON",
        ]

        for payload, reason in zip(payloads, expected, strict=True):
            with self.subTest(reason=reason):
                provider = OllamaAgentProvider(
                    "http://host",
                    "model",
                    transport=lambda *_, value=payload: value,
                )
                decision = await provider.decide(request())
                self.assertIsInstance(decision, InvalidDecision)
                self.assertEqual(decision.reason, reason)
                self.assertLessEqual(len(decision.raw or ""), 500)

    async def test_transport_errors_and_timeout_raise(self):
        def broken(*_):
            raise ConnectionError("offline")

        with self.assertRaisesRegex(ConnectionError, "offline"):
            await OllamaAgentProvider(
                "http://host", "model", transport=broken
            ).decide(request())

        def slow(*_):
            time.sleep(0.05)
            return response({"type": "final", "facts": "late"})

        with self.assertRaises(asyncio.TimeoutError):
            await OllamaAgentProvider(
                "http://host", "model", timeout_s=0.01, transport=slow
            ).decide(request())

    async def test_observer_runs_for_each_decision_and_is_isolated(self):
        observed = []
        payloads = iter(
            [
                response({"type": "tool_call", "skill": "fs.read", "args": {}}),
                response({"type": "final", "facts": "done"}),
            ]
        )

        def observer(call):
            observed.append(call)
            if len(observed) == 1:
                raise RuntimeError("observer failure")

        provider = OllamaAgentProvider(
            "http://host",
            "model",
            transport=lambda *_: next(payloads),
            observer=observer,
        )
        with self.assertLogs("models.agent_provider", level="WARNING") as captured:
            self.assertIsInstance(await provider.decide(request()), ToolCall)
        self.assertIsInstance(await provider.decide(request()), FinalResult)
        self.assertIn("observer failed", captured.output[0].lower())
        self.assertEqual([item.decision_kind for item in observed], ["tool_call", "final"])
        self.assertTrue(all(item.model == "model" for item in observed))
        self.assertTrue(all(len(item.raw_capped) <= 500 for item in observed))

    async def test_sync_transport_does_not_block_event_loop(self):
        release = threading.Event()

        def slow(*_):
            release.wait(timeout=0.5)
            return response({"type": "final", "facts": "done"})

        asyncio.get_running_loop().call_later(0.01, release.set)
        decision = await asyncio.wait_for(
            OllamaAgentProvider("http://host", "model", transport=slow).decide(request()),
            timeout=0.2,
        )
        self.assertEqual(decision, FinalResult("done"))


if __name__ == "__main__":
    unittest.main()
