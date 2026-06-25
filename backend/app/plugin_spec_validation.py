"""Validation for Sammy-generated plugin specs.

Sammy's automatic builder creates constrained HTTP JSON MCP tools. It does not
generate arbitrary protocol clients, browser automation, or executable adapters.
These checks keep model-authored specs inside that supported boundary.
"""

import json
import re
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse


ALLOWED_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

UNSUPPORTED_PROTOCOL_PATTERNS = [
    (re.compile(r"\bimap(?:4)?\b", re.IGNORECASE), "IMAP is a mail protocol, not an HTTP JSON API."),
    (re.compile(r"\bsmtp\b", re.IGNORECASE), "SMTP is a mail protocol, not an HTTP JSON API."),
    (re.compile(r"\bpop3?\b", re.IGNORECASE), "POP/POP3 is a mail protocol, not an HTTP JSON API."),
    (re.compile(r"\bstarttls\b", re.IGNORECASE), "STARTTLS is protocol-client setup, not an HTTP JSON API."),
    (re.compile(r"\bports?\s+(?:143|465|587|993|995)\b", re.IGNORECASE), "Mail-client ports require a protocol adapter."),
    (re.compile(r"\b(?:mail|email)[- ]clients?\b", re.IGNORECASE), "Mail-client configuration is not API documentation."),
    (re.compile(r"\bwebmail\b", re.IGNORECASE), "Webmail/browser UI automation is not supported by the generated HTTP builder."),
    (re.compile(r"\bbrowser (?:automation|scraping)\b", re.IGNORECASE), "Browser automation needs a reviewed adapter, not a generated HTTP tool."),
]

PROTOCOL_ENDPOINT_RE = re.compile(r"^/(?:imap|smtp|pop3?|webmail)(?:/|$)", re.IGNORECASE)


def _stringify(values: Iterable[Any]) -> str:
    parts: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            parts.append(json.dumps(value, ensure_ascii=False))
        else:
            parts.append(str(value))
    return "\n".join(parts)


def generated_tool_unsupported_reason(*values: Any) -> str:
    """Return a short reason if the request/spec is outside HTTP JSON support."""

    text = _stringify(values)
    for pattern, reason in UNSUPPORTED_PROTOCOL_PATTERNS:
        if pattern.search(text):
            return reason
    for raw in values:
        if not isinstance(raw, str):
            continue
        parsed = urlparse(raw.strip())
        host = (parsed.hostname or "").lower()
        if host.startswith(("imap.", "smtp.", "pop.", "pop3.")):
            return "This destination looks like a mail protocol server, not an HTTP JSON API origin."
        if host.startswith("mail.") and "api" not in host:
            return "This destination looks like a mail/webmail server, not an HTTP JSON API origin."
    return ""


def validate_operation_contract(operation: Dict[str, Any]) -> None:
    name = str(operation.get("name") or "operation")
    method = str(operation.get("method") or "GET").upper()
    if method not in ALLOWED_HTTP_METHODS:
        raise ValueError(f"Unsupported HTTP method for {name}: {method}")

    path = str(operation.get("path") or "").strip()
    if PROTOCOL_ENDPOINT_RE.search(path):
        raise ValueError(f"Operation {name} uses a protocol-shaped fake HTTP path: {path}")

    schema = operation.get("input_schema")
    if not isinstance(schema, dict):
        raise ValueError(f"Operation {name} needs an input schema object.")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError(f"Operation {name} input schema needs object properties.")
    property_names = set(properties)

    path_params = re.findall(r"\{([a-zA-Z0-9_]+)\}", path)
    missing_path_params = [item for item in path_params if item not in property_names]
    if missing_path_params:
        raise ValueError(
            f"Operation {name} path parameter(s) are missing from input_schema: {', '.join(missing_path_params)}"
        )

    for label in ("query_params", "body_params"):
        mapped = [str(item) for item in (operation.get(label) or [])]
        missing = [item for item in mapped if item not in property_names]
        if missing:
            raise ValueError(f"Operation {name} maps {label} not present in input_schema: {', '.join(missing)}")

    reason = generated_tool_unsupported_reason(name, operation.get("description"), path)
    if reason:
        raise ValueError(f"Operation {name} is outside the generated HTTP builder: {reason}")


def validate_generated_plugin_spec(spec: Dict[str, Any]) -> None:
    reason = generated_tool_unsupported_reason(
        spec.get("name"),
        spec.get("base_url"),
        spec.get("documentation_url"),
        spec.get("auth"),
        spec.get("operations"),
    )
    if reason:
        raise ValueError(f"Generated tool spec is outside the supported HTTP JSON builder: {reason}")

    operations = spec.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("Generated tool spec needs at least one operation.")
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("Generated tool operation must be an object.")
        validate_operation_contract(operation)
