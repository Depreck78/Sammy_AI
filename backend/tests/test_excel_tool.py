import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.excel_tool as excel_module
from tools.excel_tool import ExcelTool


def function_names(tool: ExcelTool) -> set[str]:
    return {definition["function"]["name"] for definition in tool.get_functions()}


class ExcelToolTests(unittest.TestCase):
    def test_create_is_available_without_write_opt_in_for_safe_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = ExcelTool({"allowed_directories": str(root)})

            self.assertIn("excel_create_workbook", function_names(tool))
            self.assertNotIn("excel_write_range", function_names(tool))
            with patch.object(excel_module, "DEFAULT_OUTPUT_ROOT", root / "outputs"):
                result = tool.execute(
                    "excel_create_workbook",
                    {
                        "title": "Color table",
                        "sheet_name": "Colors",
                        "headers": ["Color", "Type"],
                        "rows": [["Red", "Warm"], ["Blue", "Cool"]],
                    },
                )

            self.assertIn("Created Excel workbook", result)
            files = list((root / "outputs").glob("color_table_*.xlsx"))
            self.assertEqual(1, len(files))
            read = json.loads(tool.execute("excel_read_range", {"path": str(files[0]), "sheet_name": "Colors"}))
            self.assertEqual([["Color", "Type"], ["Red", "Warm"], ["Blue", "Cool"]], read["rows"])

    def test_create_outside_safe_output_requires_write_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            allowed.mkdir()
            tool = ExcelTool({"allowed_directories": str(allowed)})

            result = tool.execute("excel_create_workbook", {"path": str(allowed / "book.xlsx")})

            self.assertIn("requires Allow spreadsheet writes", result)

    def test_create_read_write_and_search_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "book.xlsx"
            tool = ExcelTool({"allowed_directories": str(root), "allow_writes": "true"})

            created = tool.execute(
                "excel_create_workbook",
                {
                    "path": str(path),
                    "sheet_name": "Leads",
                    "headers": ["Name", "Score"],
                    "rows": [["Ada", 98], ["Grace", 95]],
                },
            )
            self.assertIn("Created Excel workbook", created)

            read = json.loads(
                tool.execute(
                    "excel_read_range",
                    {"path": str(path), "sheet_name": "Leads", "range": "A1:B3"},
                )
            )
            self.assertEqual([["Name", "Score"], ["Ada", 98], ["Grace", 95]], read["rows"])

            written = tool.execute(
                "excel_write_range",
                {"path": str(path), "sheet_name": "Leads", "start_cell": "C2", "values": [["Founder"], ["Engineer"]]},
            )
            self.assertIn("Wrote 2 rows", written)

            search = json.loads(tool.execute("excel_search_workbook", {"path": str(path), "query": "Founder"}))
            self.assertEqual("C2", search["matches"][0]["cell"])

    def test_paths_outside_allowed_roots_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            allowed = base / "allowed"
            outside = base / "outside"
            allowed.mkdir()
            outside.mkdir()
            tool = ExcelTool({"allowed_directories": str(allowed), "allow_writes": "true"})

            result = tool.execute("excel_create_workbook", {"path": str(outside / "secret.xlsx")})

            self.assertIn("outside allowed directories", result)


if __name__ == "__main__":
    unittest.main()
