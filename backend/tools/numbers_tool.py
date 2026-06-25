import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from app.config import APP_ROOT
from app.tooling import BaseTool


CELL_RE = re.compile(r"^[A-Za-z]{1,3}[1-9][0-9]*$")


class NumbersTool(BaseTool):
    name = "numbers"
    display_name = "Numbers"
    description = "Open, create, edit, and export Apple Numbers spreadsheets through the local Numbers app."
    icon = "Table2"
    requires_auth = False

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "allowed_directories",
                "label": "Allowed directories",
                "type": "textarea",
                "placeholder": str(APP_ROOT),
                "description": "One absolute directory path per line. Defaults to this Sammy repository only.",
            },
            {
                "name": "allow_writes",
                "label": "Allow Numbers writes",
                "type": "checkbox",
                "description": "Off by default. Enable to let Sammy create, edit, or export Numbers files.",
            },
        ]

    def _allowed_roots(self) -> List[Path]:
        raw = self.credentials.get("allowed_directories")
        if isinstance(raw, str):
            values = [line.strip() for line in raw.splitlines() if line.strip()]
        elif isinstance(raw, list):
            values = raw
        else:
            values = [str(APP_ROOT)]
        roots = []
        for value in values:
            try:
                roots.append(Path(value).expanduser().resolve())
            except Exception:
                continue
        return roots or [APP_ROOT.resolve()]

    def _writes_enabled(self) -> bool:
        value = self.credentials.get("allow_writes")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _resolve_allowed(self, path: str, suffixes: List[str]) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = APP_ROOT / candidate
        candidate = candidate.resolve()
        if candidate.suffix.lower() not in suffixes:
            raise ValueError(f"Expected one of these file types: {', '.join(suffixes)}")
        for root in self._allowed_roots():
            if candidate == root or root in candidate.parents:
                return candidate
        allowed = ", ".join(str(root) for root in self._allowed_roots())
        raise PermissionError(f"Path is outside allowed directories. Allowed: {allowed}")

    def _write_guard(self) -> str:
        if self._writes_enabled():
            return ""
        return "Numbers writes are disabled. Enable Allow Numbers writes in the Numbers tool settings first."

    def _as_rows(self, rows: Any, headers: Any = None) -> List[List[Any]]:
        out: List[List[Any]] = []
        if headers:
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except json.JSONDecodeError:
                    headers = [part.strip() for part in headers.split(",") if part.strip()]
            if isinstance(headers, list):
                out.append(headers)
        if rows:
            if isinstance(rows, str):
                rows = json.loads(rows)
            if not isinstance(rows, list):
                raise ValueError("Rows must be an array of row arrays.")
            for row in rows:
                if isinstance(row, list):
                    out.append(row)
                elif isinstance(row, dict):
                    out.append(list(row.values()))
                else:
                    out.append([row])
        return out[:200]

    def _script_string(self, value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _script_value(self, value: Any) -> str:
        if value is None:
            return "missing value"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return self._script_string(str(value))

    def _run_script(self, script: str, timeout: int = 45) -> str:
        if not shutil.which("osascript"):
            raise RuntimeError("osascript is not available on this Mac.")
        result = subprocess.run(
            ["osascript"],
            input=script,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "Numbers automation failed.").strip())
        return result.stdout.strip()

    def get_functions(self) -> List[Dict[str, Any]]:
        functions = [
            self.function(
                "numbers_open_document",
                "Open a .numbers spreadsheet in the local Apple Numbers app.",
                {"path": {"type": "string", "description": "Absolute or workspace-relative .numbers path."}},
                ["path"],
            ),
        ]
        if self._writes_enabled():
            functions.extend(
                [
                    self.function(
                        "numbers_create_document",
                        "Create a Numbers spreadsheet with optional headers and rows. Requires Allow Numbers writes.",
                        {
                            "path": {"type": "string"},
                            "sheet_name": {"type": "string"},
                            "table_name": {"type": "string"},
                            "headers": {"type": "array", "items": {}},
                            "rows": {"type": "array", "items": {"type": "array", "items": {}}},
                        },
                        ["path"],
                    ),
                    self.function(
                        "numbers_set_cell",
                        "Set one cell in a Numbers document. Requires Allow Numbers writes.",
                        {
                            "path": {"type": "string"},
                            "cell": {"type": "string", "description": "Cell address such as A1."},
                            "value": {},
                            "sheet_index": {"type": "integer", "minimum": 1},
                            "table_index": {"type": "integer", "minimum": 1},
                        },
                        ["path", "cell", "value"],
                    ),
                    self.function(
                        "numbers_export_document",
                        "Export a Numbers document to xlsx, csv, or pdf. Requires Allow Numbers writes.",
                        {
                            "path": {"type": "string"},
                            "output_path": {"type": "string"},
                            "format": {"type": "string", "enum": ["xlsx", "csv", "pdf"]},
                        },
                        ["path", "output_path", "format"],
                    ),
                ]
            )
        return functions

    def _open_document(self, parameters: Dict[str, Any]) -> str:
        path = self._resolve_allowed(parameters["path"], [".numbers"])
        if not path.exists():
            raise FileNotFoundError(path)
        script = f"""
set sourceFile to POSIX file {self._script_string(str(path))}
tell application "Numbers"
    activate
    open sourceFile
end tell
"""
        self._run_script(script)
        return f"Opened Numbers document: {path}"

    def _create_document(self, parameters: Dict[str, Any]) -> str:
        guard = self._write_guard()
        if guard:
            return guard
        path = self._resolve_allowed(parameters["path"], [".numbers"])
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self._as_rows(parameters.get("rows"), parameters.get("headers"))
        if not rows:
            rows = [[""]]
        column_count = max(1, min(max(len(row) for row in rows), 50))
        row_count = max(1, len(rows))
        assignments = []
        for row_index, row in enumerate(rows, start=1):
            for col_index, value in enumerate(row[:column_count], start=1):
                assignments.append(f"            set value of cell {col_index} of row {row_index} to {self._script_value(value)}")
        script = f"""
set targetFile to POSIX file {self._script_string(str(path))}
tell application "Numbers"
    activate
    set theDocument to make new document
    tell theDocument
        tell active sheet
            set name to {self._script_string(parameters.get("sheet_name") or "Sheet 1")}
            delete every table
            set theTable to make new table with properties {{name:{self._script_string(parameters.get("table_name") or "Table 1")}, row count:{row_count}, column count:{column_count}}}
            tell theTable
{chr(10).join(assignments)}
            end tell
        end tell
        save in targetFile
    end tell
end tell
"""
        self._run_script(script, timeout=60)
        return f"Created Numbers document: {path}"

    def _set_cell(self, parameters: Dict[str, Any]) -> str:
        guard = self._write_guard()
        if guard:
            return guard
        path = self._resolve_allowed(parameters["path"], [".numbers"])
        if not path.exists():
            raise FileNotFoundError(path)
        cell = str(parameters.get("cell") or "").upper()
        if not CELL_RE.match(cell):
            raise ValueError("Cell must look like A1.")
        sheet_index = max(1, int(parameters.get("sheet_index") or 1))
        table_index = max(1, int(parameters.get("table_index") or 1))
        script = f"""
set sourceFile to POSIX file {self._script_string(str(path))}
tell application "Numbers"
    activate
    set theDocument to open sourceFile
    tell theDocument
        tell sheet {sheet_index}
            tell table {table_index}
                set value of cell {self._script_string(cell)} to {self._script_value(parameters.get("value"))}
            end tell
        end tell
        save
    end tell
end tell
"""
        self._run_script(script)
        return f"Set {cell} in Numbers document: {path}"

    def _export_document(self, parameters: Dict[str, Any]) -> str:
        guard = self._write_guard()
        if guard:
            return guard
        source = self._resolve_allowed(parameters["path"], [".numbers"])
        if not source.exists():
            raise FileNotFoundError(source)
        format_name = str(parameters.get("format") or "xlsx").lower()
        export_types = {"xlsx": "Microsoft Excel", "csv": "CSV", "pdf": "PDF"}
        if format_name not in export_types:
            raise ValueError("Format must be xlsx, csv, or pdf.")
        suffix = ".xlsx" if format_name == "xlsx" else f".{format_name}"
        target = self._resolve_allowed(parameters["output_path"], [suffix])
        target.parent.mkdir(parents=True, exist_ok=True)
        script = f"""
set sourceFile to POSIX file {self._script_string(str(source))}
set targetFile to POSIX file {self._script_string(str(target))}
tell application "Numbers"
    activate
    set theDocument to open sourceFile
    export theDocument to targetFile as {export_types[format_name]}
    close theDocument saving no
end tell
"""
        self._run_script(script, timeout=90)
        return f"Exported Numbers document to {target}"

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            if function_name == "numbers_open_document":
                return self._open_document(parameters)
            if function_name == "numbers_create_document":
                return self._create_document(parameters)
            if function_name == "numbers_set_cell":
                return self._set_cell(parameters)
            if function_name == "numbers_export_document":
                return self._export_document(parameters)
        except Exception as exc:
            return f"Numbers tool error: {exc}"
        return f"Unknown Numbers function: {function_name}"
