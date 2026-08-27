import math
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))

from agent.gate import GatePolicy
from agent.loop import AgentLoop, RunStatus
from models.agent_provider import OllamaAgentProvider
from routing.agent_config import load_agent_runtime_config
from skills import build_core_registry
from skills.base import SkillContext


LIVE_ENABLED = os.getenv("EMIYA_LIVE_TESTS") == "1"
GROUNDING_TOKEN = "EMIYA_B3_GROUNDING_TOKEN_7C91"


@unittest.skipUnless(LIVE_ENABLED, "set EMIYA_LIVE_TESTS=1 for real Ollama checks")
class LiveAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_reads_real_file_with_grounded_facts(self):
        runs = max(1, int(os.getenv("EMIYA_LIVE_RUNS", "10")))
        config = load_agent_runtime_config()
        model = config.model
        host = config.ollama_host
        fixture = "tests/live/agent_fixture.txt"
        successes = 0

        for index in range(runs):
            provider = OllamaAgentProvider(host=host, model=model)
            registry = build_core_registry(lambda: {})
            loop = AgentLoop(
                provider,
                registry,
                GatePolicy(),
                observation_cap_chars=3000,
            )
            result = await loop.run(
                f"read {fixture} and tell me exactly what is in the first line",
                SkillContext(list(config.allowed_roots), f"live-b3-{index}"),
            )
            used_fs_read = any(
                step.skill == "fs.read" and step.status == "ok" for step in result.steps
            )
            grounded = GROUNDING_TOKEN.casefold() in (result.final_facts or "").casefold()
            if result.status is RunStatus.COMPLETED and used_fs_read and grounded:
                successes += 1

        required = math.ceil(runs * 0.7)
        print(f"B3 live score: {successes}/{runs}; model={model}; required={required}")
        self.assertGreaterEqual(successes, required)


if __name__ == "__main__":
    unittest.main()
