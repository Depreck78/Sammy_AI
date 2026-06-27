import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.config import APP_ROOT
from tools.filesystem_tool import FileSystemTool


def function_names(tool: FileSystemTool) -> set[str]:
    return {definition["function"]["name"] for definition in tool.get_functions()}


class FileSystemToolTests(unittest.TestCase):
    def setUp(self) -> None:
        # The file tools read user folder settings via app.workspace, so they need a database.
        self._db_dir = tempfile.TemporaryDirectory()
        self._db_patch = patch.object(db, "DB_PATH", Path(self._db_dir.name) / "sammy.sqlite")
        self._db_patch.start()
        self.addCleanup(self._db_dir.cleanup)
        self.addCleanup(self._db_patch.stop)
        db.init_db()

    def test_default_is_repo_only_and_read_only(self) -> None:
        tool = FileSystemTool()

        self.assertEqual([APP_ROOT.resolve()], tool._allowed_roots())
        self.assertNotIn("filesystem_write_file", function_names(tool))

        result = tool.execute("filesystem_write_file", {"path": "tmp.txt", "content": "hello"})
        self.assertIn("File writes are disabled", result)

    def test_write_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = FileSystemTool({"allowed_directories": str(root), "allow_writes": "true"})

            self.assertIn("filesystem_write_file", function_names(tool))
            result = tool.execute("filesystem_write_file", {"path": str(root / "note.txt"), "content": "hello"})

            self.assertIn("Wrote", result)
            self.assertEqual("hello", (root / "note.txt").read_text(encoding="utf-8"))

    def test_paths_outside_allowed_roots_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            allowed = base / "allowed"
            outside = base / "outside"
            allowed.mkdir()
            outside.mkdir()
            secret = outside / "secret.txt"
            secret.write_text("nope", encoding="utf-8")

            tool = FileSystemTool({"allowed_directories": str(allowed)})
            result = tool.execute("filesystem_read_file", {"path": str(secret)})

            self.assertIn("outside allowed directories", result)


if __name__ == "__main__":
    unittest.main()
