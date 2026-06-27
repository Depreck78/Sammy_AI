import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from tools.numbers_tool import NumbersTool


def function_names(tool: NumbersTool) -> set[str]:
    return {definition["function"]["name"] for definition in tool.get_functions()}


class NumbersToolTests(unittest.TestCase):
    def setUp(self) -> None:
        # The file tools read user folder settings via app.workspace, so they need a database.
        self._db_dir = tempfile.TemporaryDirectory()
        self._db_patch = patch.object(db, "DB_PATH", Path(self._db_dir.name) / "sammy.sqlite")
        self._db_patch.start()
        self.addCleanup(self._db_dir.cleanup)
        self.addCleanup(self._db_patch.stop)
        db.init_db()

    def test_writes_require_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = NumbersTool({"allowed_directories": str(root)})

            # The create function is now always exposed (so the model can attempt it and get an
            # actionable message), but it must refuse to actually write until writes are enabled.
            self.assertIn("numbers_open_document", function_names(tool))
            self.assertIn("numbers_create_document", function_names(tool))

            with patch.object(tool, "_run_script", side_effect=AssertionError("should not run a script")):
                result = tool.execute("numbers_create_document", {"path": str(root / "book.numbers")})

            self.assertIn("Numbers writes are disabled", result)

    def test_create_document_builds_numbers_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "book.numbers"
            tool = NumbersTool({"allowed_directories": str(root), "allow_writes": "true"})
            scripts = []

            with patch.object(tool, "_run_script", side_effect=lambda script, timeout=45: scripts.append(script) or ""):
                result = tool.execute(
                    "numbers_create_document",
                    {
                        "path": str(path),
                        "sheet_name": "Pipeline",
                        "table_name": "Deals",
                        "headers": ["Name", "Value"],
                        "rows": [["Ada", 100]],
                    },
                )

            self.assertIn("Created Numbers document", result)
            self.assertIn('set name to "Pipeline"', scripts[0])
            self.assertIn('name:"Deals"', scripts[0])
            self.assertIn('set value of cell 1 of row 1 to "Name"', scripts[0])
            self.assertIn('set value of cell 2 of row 2 to 100', scripts[0])

    def test_export_document_builds_export_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.numbers"
            source.write_text("", encoding="utf-8")
            target = root / "book.xlsx"
            tool = NumbersTool({"allowed_directories": str(root), "allow_writes": "true"})
            scripts = []

            with patch.object(tool, "_run_script", side_effect=lambda script, timeout=45: scripts.append(script) or ""):
                result = tool.execute(
                    "numbers_export_document",
                    {"path": str(source), "output_path": str(target), "format": "xlsx"},
                )

            self.assertIn("Exported Numbers document", result)
            self.assertIn("export theDocument to targetFile as Microsoft Excel", scripts[0])


if __name__ == "__main__":
    unittest.main()
