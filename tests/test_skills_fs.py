import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

from skills.base import SkillContext
from skills.fs import FsListSkill, FsReadSkill
from skills.registry import SkillRegistry


class FilesystemSkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "foo"
        self.root.mkdir()
        self.context = SkillContext(allowed_roots=[self.root], run_id="fs-test")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _registry(skill):
        registry = SkillRegistry()
        registry.register(skill)
        return registry

    async def test_read_allows_file_inside_root(self):
        target = self.root / "file.txt"
        target.write_text("inside", encoding="utf-8")

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": "file.txt"},
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["content"], "inside")
        self.assertEqual(result.data["bytes_read"], 6)
        self.assertFalse(result.truncated)

    async def test_parent_escape_is_denied(self):
        (self.base / "outside.txt").write_text("outside", encoding="utf-8")

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": "../outside.txt"},
            self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "path is outside allowed roots")

    async def test_absolute_outside_path_is_denied(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": str(outside.resolve())},
            self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "path is outside allowed roots")

    async def test_similar_prefix_directory_is_denied(self):
        collision = self.base / "foobar"
        collision.mkdir()
        target = collision / "file.txt"
        target.write_text("outside", encoding="utf-8")

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": str(target.resolve())},
            self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "path is outside allowed roots")

    async def test_normalized_inside_path_is_allowed(self):
        target = self.root / "file.txt"
        target.write_text("normalized", encoding="utf-8")
        (self.root / "sub").mkdir()

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": os.path.join("sub", "..", "file.txt")},
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["content"], "normalized")

    async def test_symlink_or_junction_escape_is_denied(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": "link/secret.txt"},
            self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "path is outside allowed roots")

    async def test_read_cap_is_applied_before_decode(self):
        target = self.root / "utf8.txt"
        target.write_bytes("é".encode("utf-8"))

        result = await self._registry(FsReadSkill(max_bytes=1)).execute(
            "fs.read",
            {"path": "utf8.txt"},
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertEqual(result.data["bytes_read"], 1)
        self.assertEqual(result.data["content"], "\ufffd")

    async def test_invalid_utf8_does_not_fail(self):
        target = self.root / "binary.dat"
        target.write_bytes(b"prefix\xffsuffix")

        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {"path": "binary.dat"},
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertIn("\ufffd", result.data["content"])

    async def test_list_is_non_recursive_and_structured(self):
        (self.root / "a.txt").write_text("abc", encoding="utf-8")
        (self.root / "folder").mkdir()
        (self.root / "folder" / "nested.txt").write_text("hidden", encoding="utf-8")

        result = await self._registry(FsListSkill()).execute(
            "fs.list",
            {"path": "."},
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.data["entries"],
            [
                {"name": "a.txt", "type": "file", "size": 3},
                {"name": "folder", "type": "directory", "size": None},
            ],
        )

    async def test_list_cap_sets_truncated(self):
        for index in range(6):
            (self.root / f"{index}.txt").write_text(str(index), encoding="utf-8")

        result = await self._registry(FsListSkill(max_entries=3)).execute(
            "fs.list",
            {"path": "."},
            self.context,
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertEqual(result.data["count"], 3)
        self.assertEqual(len(result.data["entries"]), 3)

    async def test_missing_path_argument_is_rejected_before_skill(self):
        result = await self._registry(FsReadSkill()).execute(
            "fs.read",
            {},
            self.context,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "invalid arguments: args.path is required")


if __name__ == "__main__":
    unittest.main()
