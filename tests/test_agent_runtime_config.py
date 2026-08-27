import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from routing.agent_config import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_OLLAMA_HOST,
    AgentRuntimeConfig,
    load_agent_runtime_config,
    log_agent_runtime_config,
)


class AgentRuntimeConfigTests(unittest.TestCase):
    def test_default_model_and_repository_root(self):
        config = load_agent_runtime_config({}, repo_root=ROOT)

        self.assertEqual(DEFAULT_AGENT_MODEL, "gemma4:e4b")
        self.assertEqual(config.model, "gemma4:e4b")
        self.assertEqual(config.ollama_host, DEFAULT_OLLAMA_HOST)
        self.assertEqual(config.allowed_roots, (ROOT.resolve(),))
        root = config.allowed_roots[0]
        self.assertNotEqual(root, Path(root.anchor))
        self.assertNotEqual(root, Path.home().resolve())
        self.assertNotEqual(root, (Path.home() / "Desktop").resolve())

    def test_explicit_model_override_is_used_exactly(self):
        config = load_agent_runtime_config(
            {"EMIYA_AGENT_MODEL": "some-test-model"},
            repo_root=ROOT,
        )
        self.assertEqual(config.model, "some-test-model")

    def test_root_override_is_resolved_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            raw = os.pathsep.join((first, second, first, ""))
            config = load_agent_runtime_config(
                {"EMIYA_AGENT_ROOTS": raw},
                repo_root=ROOT,
            )
            self.assertEqual(
                config.allowed_roots,
                (Path(first).resolve(), Path(second).resolve()),
            )

    def test_invalid_or_empty_override_uses_repository_root(self):
        missing = ROOT / "does-not-exist-b31"
        raw = os.pathsep.join(("", " ", str(missing)))
        with self.assertLogs("routing.agent_config", level="WARNING") as captured:
            config = load_agent_runtime_config(
                {"EMIYA_AGENT_ROOTS": raw},
                repo_root=ROOT,
            )

        self.assertEqual(config.allowed_roots, (ROOT.resolve(),))
        self.assertTrue(any("using repository root" in line for line in captured.output))

    def test_startup_log_contains_effective_values(self):
        config = AgentRuntimeConfig(
            model="model-x",
            ollama_host="http://ollama.test:11434",
            allowed_roots=(ROOT.resolve(),),
        )
        with self.assertLogs("routing.agent_config", level="WARNING") as captured:
            log_agent_runtime_config(config)

        message = captured.output[0]
        self.assertIn("model=model-x", message)
        self.assertIn("ollama=http://ollama.test:11434", message)
        self.assertIn("roots=", message)
        self.assertIn(ROOT.name, message)


if __name__ == "__main__":
    unittest.main()
