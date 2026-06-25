import json
import ipaddress
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from . import db
from .codex_plugins import SAMMY_PLUGIN_HOME, plugin_tool_name
from .plugin_spec_validation import (
    ALLOWED_HTTP_METHODS,
    generated_tool_unsupported_reason,
    validate_generated_plugin_spec,
    validate_operation_contract,
)


PROPOSE_FUNCTION_NAME = "sammy_plugin_propose"
BUILD_FUNCTION_NAME = "sammy_plugin_build"
BUILDER_TOOL_NAME = "sammy_plugin_builder"
ALLOWED_AUTH_TYPES = {"none", "bearer", "api_key", "basic"}
ALLOWED_METHODS = ALLOWED_HTTP_METHODS
SUPPORTED_API_TYPES = {"http"}
APPROVAL_RE = re.compile(
    r"\b(yes|approve|approved|build it|build this|create it|go ahead|do it|proceed|make it)\b",
    re.IGNORECASE,
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return (slug or "sammy-plugin")[:64].rstrip("-")


def _identifier(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_").lower()
    if not cleaned:
        cleaned = "operation"
    if cleaned[0].isdigit():
        cleaned = f"op_{cleaned}"
    return cleaned[:64]


def _validate_url(url: str, allow_private_network: bool, label: str = "Base URL") -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{label} cannot contain credentials, a query, or a fragment.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        private_address = address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    except ValueError:
        private_address = False
    private_host = (
        private_address
        or parsed.hostname.endswith(".internal")
        or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        or parsed.hostname.endswith(".local")
        or parsed.hostname.startswith("10.")
        or parsed.hostname.startswith("192.168.")
    )
    if parsed.scheme != "https" and not (allow_private_network and private_host):
        raise ValueError(f"{label} must use HTTPS unless the approved service is on the local network.")
    if private_host and not allow_private_network:
        raise ValueError(f"{label} points to a private host that was not included in the proposal.")
    return parsed.geturl().rstrip("/")


def _explicit_approval(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return 0 < len(normalized) <= 240 and bool(APPROVAL_RE.search(normalized))


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": PROPOSE_FUNCTION_NAME,
                "description": (
                    "Propose a new local tool when no installed tool can perform the user's requested app/service task. "
                    "This records a proposal only and does not create files. Call it once, then explain the proposal and wait."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service_name": {"type": "string"},
                        "goal": {"type": "string", "description": "What the user wants this integration to accomplish."},
                        "capabilities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific read/write actions the tool would receive.",
                        },
                        "base_url": {"type": "string", "description": "Exact API origin or approved local service URL."},
                        "documentation_url": {"type": "string"},
                        "api_type": {
                            "type": "string",
                            "enum": ["http", "unsupported"],
                            "description": (
                                "Use http only for a documented HTTP JSON API. Use unsupported for IMAP/SMTP/POP3, "
                                "webmail/browser-only flows, local app protocols, or anything without official HTTP API docs."
                            ),
                        },
                        "auth_type": {"type": "string", "enum": sorted(ALLOWED_AUTH_TYPES)},
                        "write_access": {
                            "type": "boolean",
                            "description": "True only when the proposal explicitly includes create/update/send/delete actions.",
                        },
                        "allow_private_network": {"type": "boolean"},
                    },
                    "required": [
                        "service_name",
                        "goal",
                        "capabilities",
                        "base_url",
                        "documentation_url",
                        "api_type",
                        "auth_type",
                        "write_access",
                    ],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": BUILD_FUNCTION_NAME,
                "description": (
                    "Build an approved proposal as a constrained local HTTP/MCP tool. In dedicated Tool Build Mode, a "
                    "public read-only proposal may be built in the same task; all other proposals require later approval."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "proposal_id": {"type": "string"},
                        "plugin_name": {"type": "string", "description": "Lowercase hyphenated tool name."},
                        "display_name": {"type": "string"},
                        "description": {"type": "string"},
                        "api_key_header": {"type": "string", "description": "Header name for api_key auth only."},
                        "operations": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 20,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "method": {"type": "string", "enum": sorted(ALLOWED_METHODS)},
                                    "path": {"type": "string", "description": "Relative API path; may contain {parameter}."},
                                    "input_schema": {"type": "object"},
                                    "query_params": {"type": "array", "items": {"type": "string"}},
                                    "body_params": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["name", "description", "method", "path", "input_schema"],
                            },
                        },
                    },
                    "required": ["proposal_id", "plugin_name", "display_name", "description", "operations"],
                },
            },
        },
    ]


def is_builder_call(function_name: str) -> bool:
    return function_name in {PROPOSE_FUNCTION_NAME, BUILD_FUNCTION_NAME}


def context(conversation_id: str, build_mode: bool = False) -> str:
    pending = db.latest_plugin_proposal(conversation_id, status="pending") if conversation_id else None
    pending_text = ""
    if pending:
        pending_text = (
            f"\nPending proposal {pending['id']}: {pending['service_name']} for {pending['goal']}. "
            f"Approved destination: {pending['base_url']}; auth: {pending['auth_type']}; "
            f"access mode: {'read/write' if pending.get('write_access') else 'read-only'}."
        )
    standard_policy = (
        "Tool creation policy: First use all suitable installed/enabled tools. If the user requests an app or service "
        "action Sammy truly cannot perform, and that service has a documented HTTP JSON API, call sammy_plugin_propose once. Do not propose "
        "a tool for ordinary reasoning, local writing, or a capability an existing tool already supplies. Clearly tell the "
        "user the service destination, requested read/write capabilities, credential type, and that the tool will be stored "
        "locally. Stop and wait for explicit approval. Outside dedicated Tool Build Mode, never call sammy_plugin_build in "
        "the proposal turn. On a later turn, call "
        "sammy_plugin_build only when the latest user message explicitly approves that pending proposal. Generated tools may "
        "declare HTTP JSON operations but may not contain model-authored executable code or secrets. The base_url must be the "
        "official API origin, not a marketing site, login page, webmail host, IMAP/SMTP server, or generic website. OAuth-only integrations need a "
        "manually reviewed adapter and should not be claimed as automatically buildable. Verify endpoint paths and parameter "
        "schemas against the service's official HTTP API documentation before building; use web search when enabled, otherwise ask "
        "the user for the official documentation URL. Never invent API operations. If the docs only describe IMAP, SMTP, POP3, "
        "mail-client settings, webmail UI behavior, browser automation, or local app protocols, set api_type=unsupported or tell "
        "the user Sammy needs a reviewed adapter/template instead of building a generated tool. Never turn protocol names into "
        "fake HTTP paths such as /imap/list or /smtp/send."
        + pending_text
    )
    if not build_mode:
        return standard_policy
    return (
        standard_policy
        + "\n\nTOOL BUILD MODE: The user deliberately opened Sammy's dedicated tool builder. Focus only on designing, "
        "verifying, generating, and connecting the requested tool. Start by identifying the service, exact goal, "
        "official API base URL and documentation, authentication type, and specific operations. Use enabled web search "
        "to verify official documentation when possible; otherwise ask for the official documentation URL. Generated "
        "tools are declarative MCP integrations with 1 to 20 HTTP operations using GET, POST, PUT, PATCH, or DELETE; "
        "authentication may be none, bearer, API key, or basic. Never invent endpoints or generate arbitrary executable "
        "code. This builder is HTTP JSON only: do not build IMAP/SMTP/POP3 mail clients, webmail/browser automation, OAuth-only "
        "connectors, local app protocols, or undocumented services. If the requested service falls into one of those cases, say "
        "what connector type would be needed and stop. For a public HTTPS, read-only integration, the user's message in this dedicated mode authorizes local file "
        "creation: call sammy_plugin_propose, then after receiving its proposal ID call sammy_plugin_build in the same task. "
        "For any write access or private-network destination, propose the exact destination, operations, and access first, "
        "then stop and wait for explicit approval in a later user message. After a successful build, clearly say that the "
        "tool was installed locally and enabled for the current agent, and mention credential setup only when required."
    )


def _proposal(
    conversation_id: str,
    agent: Dict[str, Any],
    arguments: Dict[str, Any],
    build_mode: bool = False,
) -> Dict[str, Any]:
    service = str(arguments.get("service_name") or "").strip()[:120]
    goal = str(arguments.get("goal") or "").strip()[:800]
    capabilities = [str(item).strip()[:160] for item in (arguments.get("capabilities") or []) if str(item).strip()][:20]
    auth_type = str(arguments.get("auth_type") or "none").lower()
    api_type = str(arguments.get("api_type") or "http").lower()
    allow_private = bool(arguments.get("allow_private_network", False))
    write_access = bool(arguments.get("write_access", False))
    if not service or not goal or not capabilities:
        raise ValueError("A tool proposal needs a service, goal, and specific capabilities.")
    if api_type not in SUPPORTED_API_TYPES:
        raise ValueError(
            "Sammy's automatic tool builder currently supports only documented HTTP JSON APIs. "
            "This service needs a reviewed adapter/template instead of a generated HTTP tool."
        )
    if auth_type not in ALLOWED_AUTH_TYPES:
        raise ValueError("Unsupported authentication type. Use none, bearer, api_key, or basic.")
    base_url = _validate_url(str(arguments.get("base_url") or ""), allow_private)
    documentation_url = str(arguments.get("documentation_url") or "").strip()
    if not documentation_url:
        raise ValueError("A tool proposal needs the official HTTP API documentation URL before Sammy can build it.")
    if documentation_url:
        documentation_url = _validate_url(documentation_url, False, "Documentation URL")
    unsupported_reason = generated_tool_unsupported_reason(service, goal, capabilities, base_url, documentation_url)
    if unsupported_reason:
        raise ValueError(
            "Sammy's generated tool builder cannot build this as an HTTP tool. "
            f"{unsupported_reason} Use or add a reviewed adapter/template for this connector type."
        )
    existing = db.latest_plugin_proposal(conversation_id, status="pending")
    if existing:
        can_continue = build_mode and not existing.get("write_access") and not existing.get("allow_private_network")
        return {
            "ok": True,
            "proposal_id": existing["id"],
            "status": "pending",
            "content": (
                f"A tool proposal already exists: {existing['service_name']} ({existing['id']}). "
                + ("Continue with the build using this proposal ID." if can_continue else "It is waiting for user approval.")
            ),
        }
    proposal = db.create_plugin_proposal(
        {
            "conversation_id": conversation_id,
            "agent_id": str(agent.get("id") or "default"),
            "source_user_message_id": db.latest_user_message_id(conversation_id),
            "service_name": service,
            "goal": goal,
            "capabilities": capabilities,
            "base_url": base_url,
            "documentation_url": documentation_url,
            "auth_type": auth_type,
            "write_access": write_access,
            "allow_private_network": allow_private,
        }
    )
    access = ", ".join(capabilities)
    access_mode = "read/write" if write_access else "read-only"
    can_continue = build_mode and not write_access and not allow_private
    return {
        "ok": True,
        "proposal_id": proposal["id"],
        "status": "pending",
        "content": (
            f"Tool proposal {proposal['id']} is {'ready to build' if can_continue else 'waiting for user approval'}. Service: {service}. "
            f"Destination: {base_url}. Access mode: {access_mode}. Capabilities: {access}. Authentication: {auth_type}. "
            + (
                "Dedicated Tool Build Mode authorizes this public read-only proposal. Continue by calling "
                "sammy_plugin_build with this proposal ID and the verified operations."
                if can_continue
                else "Explain this clearly and stop; do not build anything in this turn."
            )
        ),
    }


def _auth_fields(auth_type: str) -> List[Dict[str, Any]]:
    if auth_type == "bearer":
        return [{"name": "access_token", "label": "Access token", "type": "password", "description": "Stored encrypted by Sammy."}]
    if auth_type == "api_key":
        return [{"name": "api_key", "label": "API key", "type": "password", "description": "Stored encrypted by Sammy."}]
    if auth_type == "basic":
        return [
            {"name": "username", "label": "Username", "type": "text"},
            {"name": "password", "label": "Password", "type": "password", "description": "Stored encrypted by Sammy."},
        ]
    return []


def _validated_operations(raw_operations: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_operations, list) or not 1 <= len(raw_operations) <= 20:
        raise ValueError("A generated tool needs between 1 and 20 declared operations.")
    operations: List[Dict[str, Any]] = []
    names = set()
    for raw in raw_operations:
        if not isinstance(raw, dict):
            raise ValueError("Every operation must be an object.")
        name = _identifier(raw.get("name"))
        if name in names:
            raise ValueError(f"Duplicate operation name: {name}")
        names.add(name)
        method = str(raw.get("method") or "GET").upper()
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Unsupported HTTP method: {method}")
        path = str(raw.get("path") or "").strip()
        if not path.startswith("/") or "://" in path or ".." in path or "?" in path or "#" in path:
            raise ValueError(f"Operation {name} must use a safe relative API path.")
        schema = raw.get("input_schema")
        if not isinstance(schema, dict):
            raise ValueError(f"Operation {name} needs an input schema object.")
        schema = dict(schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            raise ValueError(f"Operation {name} input schema must be an object schema.")
        properties = set(schema["properties"])
        path_params = re.findall(r"\{([a-zA-Z0-9_]+)\}", path)
        if any(item not in properties for item in path_params):
            missing = ", ".join(item for item in path_params if item not in properties)
            raise ValueError(f"Operation {name} path parameter(s) not present in input schema: {missing}.")
        query_params = [_identifier(item) for item in (raw.get("query_params") or [])]
        body_params = [_identifier(item) for item in (raw.get("body_params") or [])]
        if any(item not in properties for item in [*query_params, *body_params]):
            raise ValueError(f"Operation {name} maps a parameter not present in its input schema.")
        operation = {
            "name": name,
            "description": str(raw.get("description") or name).strip()[:500],
            "method": method,
            "path": path,
            "input_schema": schema,
            "query_params": query_params,
            "body_params": body_params,
        }
        validate_operation_contract(operation)
        operations.append(operation)
    if len(json.dumps(operations)) > 100_000:
        raise ValueError("Generated tool specification is too large.")
    return operations


def _build(
    conversation_id: str,
    agent: Dict[str, Any],
    current_user_text: str,
    arguments: Dict[str, Any],
    build_mode: bool = False,
) -> Dict[str, Any]:
    proposal_id = str(arguments.get("proposal_id") or "")
    try:
        proposal = db.get_plugin_proposal(proposal_id)
    except KeyError as exc:
        raise ValueError("Tool proposal was not found.") from exc
    if proposal["conversation_id"] != conversation_id or proposal["agent_id"] != str(agent.get("id") or "default"):
        raise ValueError("Tool proposal does not belong to this conversation and agent.")
    if proposal["status"] == "built":
        raise ValueError("This tool proposal has already been built.")
    if proposal["status"] not in {"pending", "approved"}:
        raise ValueError(f"Tool proposal is {proposal['status']} and cannot be built.")
    current_user_message_id = db.latest_user_message_id(conversation_id)
    same_turn = bool(current_user_message_id and current_user_message_id == proposal.get("source_user_message_id"))
    mode_authorized = bool(
        build_mode
        and not proposal.get("write_access")
        and not proposal.get("allow_private_network")
    )
    if not current_user_message_id or (same_turn and not mode_authorized):
        raise ValueError("Tool approval must come in a new user message after the proposal.")
    if proposal["status"] == "pending" and not (mode_authorized or _explicit_approval(current_user_text)):
        raise ValueError("The latest user message did not explicitly approve this tool proposal.")
    if proposal["status"] == "pending":
        proposal = db.update_plugin_proposal(proposal_id, {"status": "approved"})

    plugin_name = _slug(arguments.get("plugin_name") or proposal["service_name"])
    display_name = str(arguments.get("display_name") or proposal["service_name"]).strip()[:100]
    description = str(arguments.get("description") or proposal["goal"]).strip()[:500]
    operations = _validated_operations(arguments.get("operations"))
    if not proposal.get("write_access") and any(item["method"] != "GET" for item in operations):
        raise ValueError("This proposal was approved as read-only, so write-capable HTTP operations are blocked.")
    auth_type = proposal["auth_type"]
    api_key_header = str(arguments.get("api_key_header") or "X-API-Key").strip()
    if auth_type == "api_key" and (not re.fullmatch(r"[A-Za-z0-9-]{1,80}", api_key_header)):
        raise ValueError("API key header contains unsupported characters.")

    SAMMY_PLUGIN_HOME.mkdir(parents=True, exist_ok=True)
    target = SAMMY_PLUGIN_HOME / plugin_name
    if target.exists():
        raise ValueError(f"A local tool named '{plugin_name}' already exists. Sammy will not overwrite it automatically.")
    staging = Path(tempfile.mkdtemp(prefix=f".{plugin_name}-", dir=str(SAMMY_PLUGIN_HOME)))
    try:
        manifest_dir = staging / ".sammy-plugin"
        manifest_dir.mkdir()
        auth_fields = _auth_fields(auth_type)
        manifest = {
            "name": plugin_name,
            "version": "0.1.0",
            "description": description,
            "interface": {
                "displayName": display_name,
                "shortDescription": description,
                "developerName": "Sammy",
                "category": "Integrations",
                "brandColor": "#B96532",
                "capabilities": proposal["capabilities"],
                "authFields": auth_fields,
                "generatedBySammy": True,
            },
            "mcpServers": ".mcp.json",
        }
        mcp = {
            "mcpServers": {
                plugin_name: {
                    "command": sys.executable,
                    "args": ["server.py"],
                    "cwd": ".",
                }
            }
        }
        spec = {
            "name": plugin_name,
            "base_url": proposal["base_url"],
            "documentation_url": proposal["documentation_url"],
            "allow_private_network": proposal["allow_private_network"],
            "write_access": proposal["write_access"],
            "auth": {"type": auth_type, "header": api_key_header if auth_type == "api_key" else ""},
            "operations": operations,
        }
        validate_generated_plugin_spec(spec)
        (manifest_dir / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (staging / ".mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
        (staging / "plugin-spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        runtime_source = Path(__file__).with_name("generated_plugin_runtime.py")
        shutil.copy2(runtime_source, staging / "server.py")
        (staging / "README.md").write_text(
            f"# {display_name}\n\n{description}\n\nGenerated locally by Sammy after explicit user approval.\n"
            f"\nAPI destination: `{proposal['base_url']}`\n",
            encoding="utf-8",
        )
        compile((staging / "server.py").read_text(encoding="utf-8"), str(staging / "server.py"), "exec")
        for path in (manifest_dir / "plugin.json", staging / ".mcp.json", staging / "plugin-spec.json"):
            json.loads(path.read_text(encoding="utf-8"))
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    db.update_plugin_proposal(proposal_id, {"status": "built", "plugin_name": plugin_name})
    db.add_enabled_tools([str(agent.get("id") or "default")], [plugin_tool_name(plugin_name)])
    credential_note = " Configure its credentials in Settings > Tools." if auth_type != "none" else ""
    return {
        "ok": True,
        "proposal_id": proposal_id,
        "status": "built",
        "plugin_name": plugin_name,
        "path": str(target),
        "content": (
            f"Built and enabled local tool '{display_name}' at {target}. "
            f"Validated {len(operations)} declared operation(s).{credential_note} "
            "It will be callable on the next turn."
        ),
    }


def handle_call(
    conversation_id: str,
    agent: Dict[str, Any],
    current_user_text: str,
    function_name: str,
    arguments: Dict[str, Any],
    build_mode: bool = False,
) -> Dict[str, Any]:
    try:
        if function_name == PROPOSE_FUNCTION_NAME:
            return _proposal(conversation_id, agent, arguments, build_mode=build_mode)
        if function_name == BUILD_FUNCTION_NAME:
            return _build(conversation_id, agent, current_user_text, arguments, build_mode=build_mode)
        raise ValueError(f"Unknown tool builder function: {function_name}")
    except Exception as exc:
        return {"ok": False, "status": "error", "content": f"Tool builder error: {exc}"}
