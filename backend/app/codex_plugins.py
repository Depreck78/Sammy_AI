import json
import os
import re
import select
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .plugin_spec_validation import validate_generated_plugin_spec

SAMMY_HOME = Path(os.environ.get("SAMMY_HOME", Path.home() / ".sammy")).expanduser()
SAMMY_PLUGIN_HOME = Path(os.environ.get("SAMMY_PLUGIN_HOME", SAMMY_HOME / "plugins")).expanduser()
LOCAL_PLUGIN_HOME = Path(os.environ.get("SAMMY_LOCAL_PLUGIN_HOME", Path.home() / "plugins")).expanduser()
INCLUDE_CODEX_CACHE = os.environ.get("SAMMY_INCLUDE_CODEX_CACHE", "").strip().lower() in {"1", "true", "yes", "on"}
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
CODEX_PLUGIN_CACHE = CODEX_HOME / "plugins" / "cache"
PLUGIN_MANIFEST_DIRS = (".sammy-plugin", ".codex-plugin")
MCP_PROTOCOL_VERSION = "2024-11-05"


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _generated_spec_error(root: Path, manifest: Dict[str, Any]) -> str:
    interface = manifest.get("interface") if isinstance(manifest.get("interface"), dict) else {}
    if not interface.get("generatedBySammy"):
        return ""
    spec_path = root / "plugin-spec.json"
    if not spec_path.exists():
        return "generated Sammy plugin is missing plugin-spec.json"
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        validate_generated_plugin_spec(spec)
    except Exception as exc:
        return str(exc)
    return ""


def _resolve_plugin_path(root: Path, value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    if not cleaned:
        cleaned = "plugin"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned[:80]


def plugin_tool_name(plugin_name: str) -> str:
    return f"sammy_plugin__{_sanitize_identifier(plugin_name)}"


def legacy_plugin_tool_name(plugin_name: str) -> str:
    return f"codex_plugin__{_sanitize_identifier(plugin_name)}"


def _schema_object(schema: Any) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(schema)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    return normalized


@dataclass
class CodexSkill:
    name: str
    description: str
    path: str


@dataclass
class CodexMcpServer:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    cwd: str = "."
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class CodexAppConnector:
    name: str
    connector_id: str


@dataclass
class CodexPlugin:
    name: str
    version: str
    description: str
    root: Path
    manifest_path: Path
    interface: Dict[str, Any] = field(default_factory=dict)
    skills: List[CodexSkill] = field(default_factory=list)
    mcp_servers: List[CodexMcpServer] = field(default_factory=list)
    app_connectors: List[CodexAppConnector] = field(default_factory=list)
    source: str = "codex_cache"

    @property
    def tool_name(self) -> str:
        return plugin_tool_name(self.name)

    @property
    def display_name(self) -> str:
        return self.interface.get("displayName") or self.name.replace("-", " ").title()

    @property
    def short_description(self) -> str:
        return self.interface.get("shortDescription") or self.description

    @property
    def brand_color(self) -> str:
        return self.interface.get("brandColor") or "#6E7681"

    @property
    def auth_fields(self) -> List[Dict[str, Any]]:
        fields = self.interface.get("authFields") or []
        return [field for field in fields if isinstance(field, dict) and field.get("name")]


def _extract_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4 :].strip()
    data: Dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data, body


def _read_skills(root: Path, manifest: Dict[str, Any]) -> List[CodexSkill]:
    skill_base = _resolve_plugin_path(root, manifest.get("skills"))
    if not skill_base or not skill_base.exists():
        return []

    skill_files: List[Path]
    if skill_base.is_file():
        skill_files = [skill_base]
    else:
        skill_files = sorted(skill_base.glob("*/SKILL.md"))
        direct = skill_base / "SKILL.md"
        if direct.exists():
            skill_files.insert(0, direct)

    skills: List[CodexSkill] = []
    for path in skill_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        meta, body = _extract_frontmatter(text)
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        first_para = next((part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()), "")
        name = meta.get("name") or (title_match.group(1).strip() if title_match else path.parent.name)
        description = meta.get("description") or re.sub(r"^#\s+.+\n?", "", first_para).strip()
        skills.append(
            CodexSkill(
                name=name,
                description=description[:700],
                path=str(path),
            )
        )
    return skills


def _read_mcp_servers(root: Path, manifest: Dict[str, Any]) -> List[CodexMcpServer]:
    path = _resolve_plugin_path(root, manifest.get("mcpServers"))
    if not path or not path.exists():
        return []
    data = _read_json(path)
    servers: List[CodexMcpServer] = []
    for name, config in (data.get("mcpServers") or {}).items():
        if not isinstance(config, dict) or not config.get("command"):
            continue
        env = {str(key): str(value) for key, value in (config.get("env") or {}).items()}
        servers.append(
            CodexMcpServer(
                name=str(name),
                command=str(config["command"]),
                args=[str(arg) for arg in _as_list(config.get("args"))],
                cwd=str(config.get("cwd") or "."),
                env=env,
            )
        )
    return servers


def _read_app_connectors(root: Path, manifest: Dict[str, Any]) -> List[CodexAppConnector]:
    path = _resolve_plugin_path(root, manifest.get("apps"))
    if not path or not path.exists():
        return []
    data = _read_json(path)
    apps: List[CodexAppConnector] = []
    for name, config in (data.get("apps") or {}).items():
        connector_id = config.get("id") if isinstance(config, dict) else None
        if connector_id:
            apps.append(CodexAppConnector(name=str(name), connector_id=str(connector_id)))
    return apps


def _plugin_manifests_under(path: Path) -> List[Path]:
    manifests: List[Path] = []
    if not path.exists():
        return manifests
    if path.is_file() and path.name == "plugin.json":
        return [path]
    for manifest_dir in PLUGIN_MANIFEST_DIRS:
        direct = path / manifest_dir / "plugin.json"
        if direct.exists():
            manifests.append(direct)
    if manifests:
        return manifests
    for manifest_dir in PLUGIN_MANIFEST_DIRS:
        manifests.extend(path.glob(f"*/{manifest_dir}/plugin.json"))
    return manifests


def _manifest_candidates() -> List[Tuple[int, str, Path]]:
    candidates: List[Tuple[int, str, Path]] = []
    if INCLUDE_CODEX_CACHE and CODEX_PLUGIN_CACHE.exists():
        candidates.extend((10, "codex_cache", path) for path in CODEX_PLUGIN_CACHE.glob("**/.codex-plugin/plugin.json"))
    if SAMMY_PLUGIN_HOME.exists():
        candidates.extend((30, "sammy_home", path) for path in _plugin_manifests_under(SAMMY_PLUGIN_HOME))
    if LOCAL_PLUGIN_HOME.exists():
        candidates.extend((40, "local", path) for path in _plugin_manifests_under(LOCAL_PLUGIN_HOME))

    extra_paths = os.pathsep.join(
        value
        for value in (os.environ.get("SAMMY_PLUGIN_PATHS", ""), os.environ.get("SAMMY_CODEX_PLUGIN_PATHS", ""))
        if value
    )
    for raw in extra_paths.split(os.pathsep):
        if not raw.strip():
            continue
        path = Path(raw.strip()).expanduser()
        candidates.extend((50, "explicit", item) for item in _plugin_manifests_under(path))
    return candidates


def discover_codex_plugins() -> List[CodexPlugin]:
    selected: Dict[str, Tuple[int, CodexPlugin]] = {}
    seen_paths = set()
    for priority, source, manifest_path in sorted(_manifest_candidates(), key=lambda item: (item[0], str(item[2]))):
        manifest_path = manifest_path.resolve()
        if manifest_path in seen_paths:
            continue
        seen_paths.add(manifest_path)
        manifest = _read_json(manifest_path)
        name = str(manifest.get("name") or manifest_path.parent.parent.name)
        root = manifest_path.parent.parent
        if _generated_spec_error(root, manifest):
            continue
        plugin = CodexPlugin(
            name=name,
            version=str(manifest.get("version") or ""),
            description=str(manifest.get("description") or ""),
            root=root,
            manifest_path=manifest_path,
            interface=manifest.get("interface") or {},
            skills=_read_skills(root, manifest),
            mcp_servers=_read_mcp_servers(root, manifest),
            app_connectors=_read_app_connectors(root, manifest),
            source=source,
        )
        current = selected.get(name)
        if current is None or priority >= current[0]:
            selected[name] = (priority, plugin)
    return [
        item[1]
        for item in sorted(
            selected.values(),
            key=lambda item: (item[1].display_name.lower(), str(item[1].root)),
        )
    ]


class McpError(RuntimeError):
    pass


class McpStdioSession:
    def __init__(
        self,
        plugin: CodexPlugin,
        server: CodexMcpServer,
        timeout: float = 8.0,
        credentials: Optional[Dict[str, Any]] = None,
    ):
        self.plugin = plugin
        self.server = server
        self.timeout = timeout
        self.credentials = credentials or {}
        self._next_id = 1
        self.process: Optional[subprocess.Popen[bytes]] = None

    def __enter__(self) -> "McpStdioSession":
        cwd = Path(self.server.cwd).expanduser()
        if not cwd.is_absolute():
            cwd = self.plugin.root / cwd
        credential_env = {
            f"SAMMY_PLUGIN_CREDENTIAL_{re.sub(r'[^A-Z0-9]+', '_', str(key).upper()).strip('_')}": str(value)
            for key, value in self.credentials.items()
            if value is not None
        }
        env = {**os.environ, **self.server.env, **credential_env}
        try:
            self.process = subprocess.Popen(
                [self.server.command, *self.server.args],
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            raise McpError(f"Unable to start MCP server {self.server.name}: {exc}") from exc

        self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "Sammy", "version": "0.1.0"},
            },
        )
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, *_: Any) -> None:
        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream:
                stream.close()

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self._read(deadline)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                if isinstance(error, dict):
                    raise McpError(error.get("message") or json.dumps(error))
                raise McpError(str(error))
            return message.get("result")

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self.request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else []
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return _mcp_result_to_text(result)

    def _write(self, message: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise McpError("MCP server is not running.")
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self.process.stdin.write(header + body)
            self.process.stdin.flush()
        except Exception as exc:
            raise McpError(f"Unable to write to MCP server {self.server.name}: {exc}") from exc

    def _read(self, deadline: float) -> Dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise McpError("MCP server is not running.")
        header = self._read_until(b"\r\n\r\n", deadline)
        match = re.search(br"content-length:\s*(\d+)", header, re.IGNORECASE)
        if not match:
            raise McpError(f"MCP server {self.server.name} sent a response without Content-Length.")
        body = self._read_exact(int(match.group(1)), deadline)
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise McpError(f"MCP server {self.server.name} sent invalid JSON: {exc}") from exc

    def _read_until(self, marker: bytes, deadline: float) -> bytes:
        data = bytearray()
        while marker not in data:
            data.extend(self._read_exact(1, deadline))
        return bytes(data)

    def _read_exact(self, length: int, deadline: float) -> bytes:
        if not self.process or not self.process.stdout:
            raise McpError("MCP server is not running.")
        chunks = bytearray()
        while len(chunks) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = self._stderr_tail()
                raise McpError(f"MCP server {self.server.name} timed out.{detail}")
            readable, _, _ = select.select([self.process.stdout], [], [], min(remaining, 0.5))
            if not readable:
                continue
            chunk = os.read(self.process.stdout.fileno(), length - len(chunks))
            if not chunk:
                detail = self._stderr_tail()
                raise McpError(f"MCP server {self.server.name} closed stdout.{detail}")
            chunks.extend(chunk)
        return bytes(chunks)

    def _stderr_tail(self) -> str:
        if not self.process or not self.process.stderr:
            return ""
        try:
            readable, _, _ = select.select([self.process.stderr], [], [], 0)
            if not readable:
                return ""
            text = self.process.stderr.read(4000).decode("utf-8", errors="replace").strip()
            return f" stderr: {text}" if text else ""
        except Exception:
            return ""


def _mcp_result_to_text(result: Any) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, indent=2, ensure_ascii=False)
    content = result.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, indent=2, ensure_ascii=False))
        return "\n".join(part for part in parts if part)
    return json.dumps(result, indent=2, ensure_ascii=False)


_MCP_TOOL_CACHE: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]], str]] = {}


class CodexPluginTool:
    icon = "Plug"
    requires_auth = False

    def __init__(self, plugin: CodexPlugin):
        self.credentials: Dict[str, Any] = {}
        self.plugin = plugin
        self.name = plugin.tool_name
        self.display_name = plugin.display_name
        self.description = plugin.short_description
        self.requires_auth = bool(plugin.auth_fields)
        self._function_routes: Dict[str, Tuple[CodexMcpServer, str]] = {}

    def _list_mcp_tools(self, server: CodexMcpServer) -> Tuple[List[Dict[str, Any]], str]:
        key = (str(self.plugin.manifest_path), server.name)
        cached = _MCP_TOOL_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1], cached[2]
        try:
            with McpStdioSession(self.plugin, server) as session:
                tools = session.list_tools()
            error = ""
        except Exception as exc:
            tools = []
            error = str(exc)
        _MCP_TOOL_CACHE[key] = (time.monotonic(), tools, error)
        return tools, error

    def get_functions(self) -> List[Dict[str, Any]]:
        definitions: List[Dict[str, Any]] = []
        self._function_routes = {}
        for server in self.plugin.mcp_servers:
            tools, _ = self._list_mcp_tools(server)
            for tool in tools:
                mcp_name = str(tool.get("name") or "")
                if not mcp_name:
                    continue
                function_name = (
                    f"sammy_mcp__{_sanitize_identifier(self.plugin.name)}__"
                    f"{_sanitize_identifier(server.name)}__{_sanitize_identifier(mcp_name)}"
                )
                self._function_routes[function_name] = (server, mcp_name)
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": function_name,
                            "description": f"[{self.plugin.display_name}] {tool.get('description') or mcp_name}",
                            "parameters": _schema_object(tool.get("inputSchema")),
                        },
                    }
                )
        return definitions

    def execute(self, function_name: str, parameters: Dict[str, Any]) -> str:
        if function_name not in self._function_routes:
            self.get_functions()
        route = self._function_routes.get(function_name)
        if not route:
            return f"Tool function '{function_name}' is not available."
        server, mcp_tool_name = route
        try:
            with McpStdioSession(self.plugin, server, timeout=20, credentials=self.credentials) as session:
                return session.call_tool(mcp_tool_name, parameters or {})
        except Exception as exc:
            return f"Tool '{self.plugin.display_name}' MCP error: {exc}"

    def metadata(self) -> Dict[str, Any]:
        functions = self.get_functions()
        errors: Dict[str, str] = {}
        for server in self.plugin.mcp_servers:
            _, error = self._list_mcp_tools(server)
            if error:
                errors[server.name] = error
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "icon": self.icon,
            "requires_auth": self.requires_auth,
            "auth_fields": self.get_auth_fields(),
            "functions": functions,
            "kind": "external_plugin",
            "plugin": {
                "name": self.plugin.name,
                "version": self.plugin.version,
                "root": str(self.plugin.root),
                "manifest": str(self.plugin.manifest_path),
                "source": self.plugin.source,
                "brand_color": self.plugin.brand_color,
                "developer": self.plugin.interface.get("developerName") or "",
                "category": self.plugin.interface.get("category") or "",
                "capabilities": self.plugin.interface.get("capabilities") or [],
                "default_prompt": self.plugin.interface.get("defaultPrompt") or [],
            },
            "skills": [skill.__dict__ for skill in self.plugin.skills],
            "mcp_servers": [server.__dict__ for server in self.plugin.mcp_servers],
            "app_connectors": [connector.__dict__ for connector in self.plugin.app_connectors],
            "status_message": "; ".join(f"{name}: {error}" for name, error in errors.items()),
        }

    def get_auth_fields(self) -> List[Dict[str, Any]]:
        return self.plugin.auth_fields

    def validate_auth(self, credentials: Dict[str, Any]) -> bool:
        return not self.requires_auth or all(
            bool(credentials.get(str(field.get("name") or ""))) for field in self.get_auth_fields()
        )

    def injection_text(self) -> str:
        lines = [
            f"External tool enabled: {self.plugin.display_name} ({self.plugin.name})",
            f"Description: {self.plugin.description}",
        ]
        if self.plugin.skills:
            lines.append("Skills available:")
            for skill in self.plugin.skills:
                lines.append(f"- {skill.name}: {skill.description}")
        if self.plugin.mcp_servers:
            names = ", ".join(server.name for server in self.plugin.mcp_servers)
            lines.append(f"MCP servers available through Sammy tools: {names}.")
        if self.plugin.app_connectors:
            apps = ", ".join(f"{app.name} ({app.connector_id})" for app in self.plugin.app_connectors)
            if self.plugin.name == "gmail":
                lines.append(
                    "Gmail app connector metadata is installed. In Sammy, use the native Gmail tool "
                    f"functions for Gmail actions; connector metadata: {apps}."
                )
            else:
                lines.append(
                    "App connectors are metadata only unless Sammy has a native bridge for them. "
                    f"Connector metadata: {apps}."
                )
        return "\n".join(lines)


def discover_codex_plugin_tools() -> List[CodexPluginTool]:
    return [CodexPluginTool(plugin) for plugin in discover_codex_plugins()]
