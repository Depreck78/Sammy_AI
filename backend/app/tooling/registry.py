import importlib
import inspect
import pkgutil
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.codex_plugins import CodexPluginTool, discover_codex_plugin_tools, legacy_plugin_tool_name, plugin_tool_name
from .base import BaseTool


CredentialsProvider = Callable[[str], Dict[str, Any]]

PLUGIN_ADAPTERS: Dict[str, str] = {
    "gmail": "gmail",
    "github": "github",
}

NATIVE_PLUGIN_REPLACEMENTS: Dict[str, str] = {
    "zoho-mail": "zoho_mail",
}


class ToolRegistry:
    def __init__(self, credentials_provider: Optional[CredentialsProvider] = None):
        self.credentials_provider = credentials_provider or (lambda _: {})
        self._classes = self._discover_tool_classes()
        self._native_external_aliases = {
            alias: native_name
            for plugin_name, native_name in NATIVE_PLUGIN_REPLACEMENTS.items()
            if native_name in self._classes
            for alias in (plugin_tool_name(plugin_name), legacy_plugin_tool_name(plugin_name))
        }
        self._external_tools = {
            tool.name: tool
            for tool in discover_codex_plugin_tools()
            if NATIVE_PLUGIN_REPLACEMENTS.get(tool.plugin.name) not in self._classes
        }
        self._legacy_external_names = {
            legacy_plugin_tool_name(tool.plugin.name): tool.name
            for tool in self._external_tools.values()
        }
        self._legacy_external_names.update(self._native_external_aliases)

    def _discover_tool_classes(self) -> Dict[str, type]:
        import tools

        classes: Dict[str, type] = {}
        for module_info in pkgutil.iter_modules(tools.__path__):
            module = importlib.import_module(f"tools.{module_info.name}")
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj is BaseTool or not issubclass(obj, BaseTool):
                    continue
                if obj.name:
                    classes[obj.name] = obj
        return dict(sorted(classes.items(), key=lambda item: item[0]))

    def names(self) -> List[str]:
        return list(self._classes.keys()) + list(self._external_tools.keys())

    def plugin_aliases(self) -> Dict[str, str]:
        return dict(self._legacy_external_names)

    def normalize_tool_name(self, name: str) -> str:
        return self._legacy_external_names.get(name, name)

    def get(self, name: str) -> Optional[BaseTool]:
        normalized = self.normalize_tool_name(name)
        if normalized in self._external_tools:
            tool = self._external_tools[normalized]
            tool.credentials = self.credentials_provider(normalized)
            return tool
        cls = self._classes.get(name)
        if not cls:
            return None
        return cls(self.credentials_provider(name))

    def _adapter_for_external_tool(self, name: str) -> Optional[str]:
        tool = self._external_tools.get(self.normalize_tool_name(name))
        if not isinstance(tool, CodexPluginTool):
            return None
        adapter_name = PLUGIN_ADAPTERS.get(tool.plugin.name)
        if adapter_name in self._classes:
            return adapter_name
        return None

    def _enabled_with_adapters(self, enabled_tools: List[str]) -> List[str]:
        expanded: List[str] = []
        for name in enabled_tools:
            if name not in expanded:
                expanded.append(name)
            adapter_name = self._adapter_for_external_tool(name)
            if adapter_name and adapter_name not in expanded:
                expanded.append(adapter_name)
        return expanded

    def _compatibility_for(self, item: Dict[str, Any], statuses: Dict[str, bool]) -> Dict[str, Any]:
        functions = item.get("functions") or []
        skills = item.get("skills") or []
        mcp_servers = item.get("mcp_servers") or []
        app_connectors = item.get("app_connectors") or []
        plugin = item.get("plugin") or {}

        if item.get("kind") != "external_plugin":
            return {
                "status": "callable",
                "label": "Callable",
                "detail": "Sammy can call this tool directly.",
                "callable": bool(functions),
            }

        if functions:
            return {
                "status": "mcp_callable",
                "label": "MCP callable",
                "detail": "Sammy can call this tool through its MCP server.",
                "callable": True,
            }

        adapter_name = PLUGIN_ADAPTERS.get(str(plugin.get("name") or ""))
        adapter = self.get(adapter_name) if adapter_name else None
        if adapter:
            adapter_functions = adapter.get_functions()
            return {
                "status": "bridged",
                "label": "Bridged",
                "detail": (
                    f"Sammy maps this app connector metadata to the {adapter.display_name or adapter.name} "
                    "tool functions. Connect the matching native Sammy tool if it needs credentials."
                ),
                "callable": bool(adapter_functions),
                "adapter_name": adapter.name,
                "adapter_display_name": adapter.display_name or adapter.name,
                "adapter_connected": True if not adapter.requires_auth else bool(statuses.get(adapter.name)),
                "adapter_functions": len(adapter_functions),
            }

        if app_connectors:
            return {
                "status": "connector_only",
                "label": "Connector only",
                "detail": (
                    "This tool declares app connector metadata, but Sammy cannot call it directly yet. "
                    "Add a native Sammy adapter or MCP server to make it executable here."
                ),
                "callable": False,
            }

        if skills:
            return {
                "status": "skills_only",
                "label": "Skills only",
                "detail": "This tool contributes instructions/context but has no callable functions in Sammy.",
                "callable": False,
            }

        if mcp_servers:
            return {
                "status": "mcp_unavailable",
                "label": "MCP unavailable",
                "detail": "This tool declares MCP servers, but Sammy could not load any functions from them.",
                "callable": False,
            }

        return {
            "status": "context_only",
            "label": "Context only",
            "detail": "No callable functions were found for this tool.",
            "callable": False,
        }

    def _credential_status(self, tool: BaseTool, credentials: Dict[str, Any]) -> Dict[str, Any]:
        saved_fields: Dict[str, bool] = {}
        visible_credentials: Dict[str, str] = {}
        get_auth_fields = getattr(tool, "get_auth_fields", None)
        auth_fields = get_auth_fields() if callable(get_auth_fields) else tool.metadata().get("auth_fields", [])

        for field in auth_fields:
            name = str(field.get("name") or "")
            if not name:
                continue
            value = credentials.get(name)
            saved_fields[name] = bool(value)
            field_name = name.lower()
            secret_named = any(part in field_name for part in ("secret", "token", "password", "api_key"))
            if field.get("type") != "password" and not secret_named and value is not None:
                visible_credentials[name] = str(value)

        return {
            "saved_auth_fields": saved_fields,
            "auth_credentials": visible_credentials,
        }

    def list_tools(self, statuses: Dict[str, bool] = None) -> List[Dict[str, Any]]:
        statuses = statuses or {}
        tools = []
        for name in self.names():
            tool = self.get(name)
            item = tool.metadata()
            credentials = self.credentials_provider(name)
            item.update(self._credential_status(tool, credentials))
            if item.get("kind") == "external_plugin":
                item["connected"] = True if not tool.requires_auth else bool(statuses.get(name))
            else:
                item["kind"] = "sammy_plugin"
                item["connected"] = True if not tool.requires_auth else bool(statuses.get(name))
                item["plugin"] = {
                    "name": name,
                    "version": "built-in",
                    "source": "sammy_builtin",
                    "root": "backend/tools",
                    "manifest": "",
                    "brand_color": "",
                    "developer": "Sammy",
                    "category": "Built-in",
                    "capabilities": ["Interactive"],
                    "default_prompt": [],
                }
            compatibility = self._compatibility_for(item, statuses)
            item["compatibility"] = compatibility
            item["callable"] = bool(compatibility.get("callable"))
            if item.get("kind") == "external_plugin" and compatibility.get("status") == "bridged":
                item["connected"] = bool(compatibility.get("adapter_connected"))
            elif item.get("kind") == "external_plugin" and compatibility.get("status") == "connector_only":
                item["connected"] = False
            tools.append(item)
        return tools

    def codex_plugin_metadata(self) -> List[Dict[str, Any]]:
        return [tool.metadata() for tool in self._external_tools.values()]

    def plugin_injections(self, enabled_tools: List[str]) -> str:
        snippets = []
        for name in enabled_tools:
            tool = self._external_tools.get(self.normalize_tool_name(name))
            if isinstance(tool, CodexPluginTool):
                snippets.append(tool.injection_text())
        if not snippets:
            return ""
        return "Installed external tool context for this agent:\n\n" + "\n\n".join(snippets)

    def function_map(self, enabled_tools: List[str]) -> Dict[str, Tuple[BaseTool, str]]:
        mapping: Dict[str, Tuple[BaseTool, str]] = {}
        for name in self._enabled_with_adapters(enabled_tools):
            tool = self.get(name)
            if not tool:
                continue
            for definition in tool.get_functions():
                function_name = definition["function"]["name"]
                mapping[function_name] = (tool, function_name)
        return mapping

    def tool_definitions(self, enabled_tools: List[str]) -> List[Dict[str, Any]]:
        definitions: List[Dict[str, Any]] = []
        for name in self._enabled_with_adapters(enabled_tools):
            tool = self.get(name)
            if tool:
                definitions.extend(tool.get_functions())
        return definitions

    def execute(self, function_name: str, parameters: Dict[str, Any], enabled_tools: List[str]) -> Dict[str, Any]:
        mapping = self.function_map(enabled_tools)
        if function_name not in mapping:
            return {
                "tool_name": "unknown",
                "function_name": function_name,
                "content": f"Tool function '{function_name}' is not enabled.",
            }
        tool, mapped_name = mapping[function_name]
        result = tool.execute(mapped_name, parameters or {})
        return {
            "tool_name": tool.name,
            "tool_display_name": tool.display_name or tool.name,
            "function_name": mapped_name,
            "content": result,
        }
