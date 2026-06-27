import fnmatch
from pathlib import Path
from typing import Any, Dict, List

from app import workspace
from app.config import APP_ROOT
from app.tooling import BaseTool


class FileSystemTool(BaseTool):
    name = "filesystem"
    display_name = "File System"
    description = "Read, list, and search files in allowed local directories. Writes require explicit opt-in."
    icon = "FolderOpen"
    requires_auth = False

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "allowed_directories",
                "label": "Allowed directories",
                "type": "textarea",
                "placeholder": str(APP_ROOT),
                "description": (
                    "One absolute directory path per line. Defaults to this Sammy repository only. "
                    "Do not add broad folders like Desktop unless you trust every enabled agent/tool and network client."
                ),
            },
            {
                "name": "allow_writes",
                "label": "Allow file writes",
                "type": "checkbox",
                "description": "Off by default. Enable only for directories where AI-generated writes are acceptable.",
            },
        ]

    def _allowed_roots(self) -> List[Path]:
        roots = list(workspace.allowed_roots())  # central, user-controlled folders
        raw = self.credentials.get("allowed_directories")
        if isinstance(raw, str):
            values = [line.strip() for line in raw.splitlines() if line.strip()]
        elif isinstance(raw, list):
            values = [value for value in raw if value]
        else:
            values = []
        for value in values:
            try:
                roots.append(Path(value).expanduser().resolve())
            except Exception:
                continue
        deduped: List[Path] = []
        for root in roots:
            if root not in deduped:
                deduped.append(root)
        return deduped

    def _writes_enabled(self) -> bool:
        value = self.credentials.get("allow_writes")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _resolve_allowed(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = workspace.relative_base() / candidate  # bare filename → default output folder
        candidate = candidate.resolve()
        for root in self._allowed_roots():
            if candidate == root or root in candidate.parents:
                return candidate
        allowed = ", ".join(str(root) for root in self._allowed_roots())
        raise PermissionError(f"Path is outside allowed directories. Allowed: {allowed}")

    def get_functions(self) -> List[Dict[str, Any]]:
        functions = [
            self.function(
                "filesystem_read_file",
                "Read a UTF-8 text file from an allowed directory.",
                {"path": {"type": "string", "description": "Absolute or workspace-relative path."}},
                ["path"],
            ),
            self.function(
                "filesystem_list_directory",
                "List files and folders in an allowed directory.",
                {"path": {"type": "string"}},
                ["path"],
            ),
            self.function(
                "filesystem_search_files",
                "Find files by filename pattern in an allowed directory.",
                {
                    "directory": {"type": "string"},
                    "pattern": {"type": "string", "description": "Filename glob, such as *.py."},
                },
                ["directory", "pattern"],
            ),
        ]
        if self._writes_enabled():
            functions.append(
                self.function(
                    "filesystem_write_file",
                    "Write UTF-8 text to a file in an allowed directory. Only available after explicit write opt-in.",
                    {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    ["path", "content"],
                )
            )
        return functions

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        try:
            if function_name == "filesystem_read_file":
                path = self._resolve_allowed(parameters["path"])
                if path.stat().st_size > 200_000:
                    return "File is larger than Sammy's 200 KB read limit."
                return path.read_text(encoding="utf-8", errors="replace")

            if function_name == "filesystem_write_file":
                if not self._writes_enabled():
                    return "File writes are disabled. Enable Allow file writes in the File System tool settings first."
                path = self._resolve_allowed(parameters["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(parameters.get("content", ""), encoding="utf-8")
                return f"Wrote {path}"

            if function_name == "filesystem_list_directory":
                path = self._resolve_allowed(parameters["path"])
                items = []
                for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:200]:
                    kind = "dir" if child.is_dir() else "file"
                    items.append(f"{kind}\t{child.name}")
                return "\n".join(items) or "Directory is empty."

            if function_name == "filesystem_search_files":
                root = self._resolve_allowed(parameters["directory"])
                pattern = parameters.get("pattern") or "*"
                matches = []
                for child in root.rglob("*"):
                    if len(matches) >= 100:
                        break
                    if child.is_file() and fnmatch.fnmatch(child.name, pattern):
                        matches.append(str(child))
                return "\n".join(matches) or "No matching files found."
        except Exception as exc:
            return f"File system tool error: {exc}"
        return f"Unknown filesystem function: {function_name}"
