"""Runtime copied into local plugins created by Sammy's constrained builder."""

import base64
import ipaddress
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 64_000
SPEC = json.loads((Path(__file__).parent / "plugin-spec.json").read_text(encoding="utf-8"))
OPERATIONS = {item["name"]: item for item in SPEC.get("operations", [])}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(_NoRedirect)


def _credential(name: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return os.environ.get(f"SAMMY_PLUGIN_CREDENTIAL_{key}", "")


def _host_is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.scheme == "http" and not SPEC.get("allow_private_network"):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError:
        return False
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        restricted = address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
        if restricted and not SPEC.get("allow_private_network"):
            return False
    return True


def _auth_headers() -> Dict[str, str]:
    auth = SPEC.get("auth") or {}
    auth_type = auth.get("type") or "none"
    if auth_type == "bearer":
        token = _credential("access_token")
        if not token:
            raise ValueError("This tool needs an access token. Add it in Settings > Tools.")
        return {"Authorization": f"Bearer {token}"}
    if auth_type == "api_key":
        value = _credential("api_key")
        if not value:
            raise ValueError("This tool needs an API key. Add it in Settings > Tools.")
        return {str(auth.get("header") or "X-API-Key"): value}
    if auth_type == "basic":
        username, password = _credential("username"), _credential("password")
        if not username or not password:
            raise ValueError("This tool needs a username and password. Add them in Settings > Tools.")
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    return {}


def _call(operation: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    method = str(operation.get("method") or "GET").upper()
    path = str(operation.get("path") or "/")
    used = set()
    for name in re.findall(r"\{([a-zA-Z0-9_]+)\}", path):
        if name not in arguments:
            raise ValueError(f"Missing path parameter: {name}")
        path = path.replace("{" + name + "}", quote(str(arguments[name]), safe=""))
        used.add(name)

    query_names = operation.get("query_params") or ([] if method != "GET" else list(arguments))
    query = {name: arguments[name] for name in query_names if name in arguments and name not in used}
    used.update(query)

    body_names = operation.get("body_params") or ([] if method in {"GET", "DELETE"} else list(arguments))
    body = {name: arguments[name] for name in body_names if name in arguments and name not in used}

    base_url = str(SPEC["base_url"]).rstrip("/")
    url = f"{base_url}/{path.lstrip('/')}"
    if query:
        url += "?" + urlencode(query, doseq=True)
    if not _host_is_allowed(url) or urlparse(url).netloc != urlparse(base_url).netloc:
        raise ValueError("Request destination is outside the approved tool host.")

    headers = {"Accept": "application/json", "User-Agent": "Sammy-Generated-Plugin/1.0", **_auth_headers()}
    payload = None
    if body:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=payload, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
            status = response.status
    except HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES)
        raise ValueError(f"Service returned HTTP {exc.code}: {raw.decode('utf-8', errors='replace')[:2000]}") from exc
    except URLError as exc:
        raise ValueError(f"Could not reach the service: {exc.reason}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raw = raw[:MAX_RESPONSE_BYTES]
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type:
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return f"HTTP {status}\n{text}"


def _read_message() -> Dict[str, Any]:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise EOFError
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.decode("ascii", errors="ignore").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_message(message: Dict[str, Any]) -> None:
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def _result(request_id: Any, result: Any) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, message: str) -> None:
    _write_message({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message}})


def main() -> None:
    while True:
        try:
            message = _read_message()
        except EOFError:
            return
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None:
            continue
        if method == "initialize":
            _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SPEC.get("name", "sammy-plugin"), "version": "0.1.0"},
                },
            )
        elif method == "tools/list":
            _result(
                request_id,
                {
                    "tools": [
                        {
                            "name": item["name"],
                            "description": item["description"],
                            "inputSchema": item.get("input_schema") or {"type": "object", "properties": {}},
                        }
                        for item in OPERATIONS.values()
                    ]
                },
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            operation = OPERATIONS.get(str(params.get("name") or ""))
            if not operation:
                _error(request_id, "Unknown tool operation.")
                continue
            try:
                text = _call(operation, params.get("arguments") or {})
                _result(request_id, {"content": [{"type": "text", "text": text}]})
            except Exception as exc:
                _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        else:
            _error(request_id, f"Unsupported MCP method: {method}")


if __name__ == "__main__":
    main()
