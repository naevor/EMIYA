import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from routing.pre_router import PreRouter
from server import EmiyaServer


class AgentServiceSpy:
    def __init__(self):
        self.calls = []
        self.loop = SimpleNamespace(provider=SimpleNamespace(name="agent-spy"))

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(reply="agent reply")


class ServerRoutingTests(unittest.TestCase):
    def server(self):
        server = EmiyaServer.__new__(EmiyaServer)
        server.pre_router = PreRouter(lambda: None)
        server.agent_service = AgentServiceSpy()
        server._record_routed_exchange = Mock()
        server._handle_chat_message = Mock(return_value="legacy reply")
        server._handle_deterministic_route = Mock(return_value="cached reply")
        server._last_reply_metadata = {}
        return server

    def test_agent_command_uses_agent_service_with_stripped_task(self):
        server = self.server()
        reply = server.handle_user_message("/agent read core/server.py")

        self.assertEqual(reply, "agent reply")
        self.assertEqual(len(server.agent_service.calls), 1)
        call = server.agent_service.calls[0]
        self.assertEqual(call["original_user_message"], "/agent read core/server.py")
        self.assertEqual(call["agent_task"], "read core/server.py")
        server._handle_chat_message.assert_not_called()
        server._record_routed_exchange.assert_called_once()

    def test_greeting_uses_unchanged_legacy_path(self):
        server = self.server()
        self.assertEqual(server.handle_user_message("hey emiya"), "legacy reply")
        server._handle_chat_message.assert_called_once_with("hey emiya")
        self.assertEqual(server._last_reply_metadata["route"], "chat")
        self.assertEqual(server.agent_service.calls, [])

    def test_cached_intent_never_uses_agent_or_chat(self):
        server = self.server()
        self.assertEqual(server.handle_user_message("CPU?"), "cached reply")
        server._handle_deterministic_route.assert_called_once()
        server._handle_chat_message.assert_not_called()
        self.assertEqual(server.agent_service.calls, [])


if __name__ == "__main__":
    unittest.main()
