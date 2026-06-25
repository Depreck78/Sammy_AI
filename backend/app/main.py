import asyncio
import base64
import json
import os
import re
import secrets
import socket
import subprocess
import time
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
import requests
import httpx

from . import db, memory, plugin_builder
from .chat_jobs import ChatJob, chat_job_manager
from .config import APP_ROOT, HOST, OAUTH_REDIRECT_URI, OLLAMA_URL, PORT, PUBLIC_URL, SAMMY_HOME, UPLOAD_DIR, dist_dir
from .generation import (
    context_input_budget,
    estimate_messages_tokens,
    render_compaction_source,
    split_history_for_compaction,
    strip_continuation_overlap,
    tool_call_signature,
    tool_result_failed,
)
from .ollama import chat_stream, list_models
from .security import decrypt_json, encrypt_json, hash_password, verify_password
from .tooling import ToolRegistry


app = FastAPI(title="Sammy", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3131", "http://127.0.0.1:3131"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SPA_INDEX_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

AUTH_COOKIE_NAME = "sammy_session"
AUTH_SESSION_SECONDS = 60 * 60 * 24 * 30
AUTH_EXEMPT_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
}
auth_sessions: Dict[str, float] = {}
AUTH_SESSIONS_PATH = SAMMY_HOME / "auth_sessions.json"


def load_auth_sessions() -> None:
    """Restore login sessions from disk so a restart (e.g. toggling LAN mode) doesn't sign out."""
    try:
        data = json.loads(AUTH_SESSIONS_PATH.read_text())
    except (OSError, ValueError):
        return
    now = time.time()
    auth_sessions.clear()
    for token, expires_at in (data or {}).items():
        try:
            if float(expires_at) > now:
                auth_sessions[token] = float(expires_at)
        except (TypeError, ValueError):
            continue


def save_auth_sessions() -> None:
    """Persist sessions (tokens are sensitive, so keep the file private)."""
    try:
        AUTH_SESSIONS_PATH.write_text(json.dumps(auth_sessions))
        AUTH_SESSIONS_PATH.chmod(0o600)
    except OSError:
        pass


class ConversationCreate(BaseModel):
    title: str = "New chat"
    model: str = ""
    agent_id: str = "default"
    mode: str = "chat"


class AgentPayload(BaseModel):
    name: str
    system_prompt: str = ""
    model: str = ""
    icon: str = ""
    enabled_tools: List[str] = []


class BestiePayload(BaseModel):
    name: str
    personality: str = ""
    avatar: str = ""  # upload file id of the (stylized) avatar image


class ChatPayload(BaseModel):
    conversation_id: Optional[str] = None
    message: str
    model: Optional[str] = None
    agent_id: Optional[str] = None
    options: Dict[str, Any] = {}
    attachments: List[str] = []
    regenerate_from: Optional[str] = None
    voice: bool = False


VOICE_REPLY_DIRECTIVE = (
    "VOICE INPUT MODE: The user is talking hands-free, but the written chat answer should still be complete "
    "and useful. Do not shorten, omit important detail, or avoid structure just because the request came by "
    "voice; the app will create a separate concise spoken summary for text-to-speech playback."
)

# Read-only work should run straight through; risky/irreversible actions should pause for the user.
# Injected whenever real tools are enabled.
AGENTIC_TOOL_DIRECTIVE = (
    "ACT, DON'T NARRATE (for safe steps): For read-only or low-risk steps — reading, searching, listing, "
    "fetching, summarizing — CALL THE TOOL IMMEDIATELY in this same turn. Do NOT reply with only a plan "
    "like \"let me check…\", \"I'll fetch…\", or \"now let me…\" and then stop; emit the tool call right "
    "then. After a tool returns, KEEP GOING in the same turn (call the next tool or give the final answer) "
    "and finish the whole read/lookup task without making the user say \"continue\".\n"
    "ASK FIRST (for risky actions): Before any action that sends, posts, deletes, modifies, creates, pays, "
    "or is otherwise hard to undo — e.g. sending an email, deleting data, editing records, messaging "
    "someone — STOP and get the user's explicit OK first. Show exactly what you'll do (for an email, the "
    "full draft: recipient, subject, body) and only perform it after they approve.\n"
    "LOCAL FILE CREATION: If the user explicitly asks you to create a local artifact and provides enough "
    "content, use the dedicated safe creation tool when one exists (for example excel_create_workbook for "
    "Excel sheets). Do not use the file-system listing tool as a substitute for creating spreadsheets."
)

# How many tool rounds one user message may chain through before Sammy stops (safety limit).
MAX_TOOL_ROUNDS = 8

# Auto-continue: if the model announces an action but doesn't call a tool, Sammy nudges it itself
# (up to this many times) instead of making the user type "continue".
MAX_AUTO_CONTINUES = 2
AUTO_CONTINUE_NUDGE = (
    "(You described what you'd do but didn't actually call a tool. If that next step is read-only, "
    "call the tool NOW instead of describing it. If it changes something or needs the user's go-ahead, "
    "ask them a direct yes/no question instead.)"
)
AUTO_CONTINUE_USER_PROMPT = (
    "Continue from your previous assistant message. It announced an action but did not complete it. "
    "For read-only work, call the appropriate enabled tool now. If the task needs approval or missing "
    "details, ask one direct question. Do not return an empty response, and do not merely say you will do it."
)
EMPTY_RESPONSE_NUDGE = (
    "Your previous response was empty. Produce a visible final answer now, or call the appropriate enabled "
    "tool if a tool is needed. Do not return only hidden reasoning or an empty message."
)
LOST_CONTEXT_NUDGE = (
    "You are still inside the user's active task and you have already used tools for it. Do not ask what to "
    "work on or ask where we left off. Use the recent user turns, compacted context, and latest tool results "
    "to complete the requested task now. If some source failed, summarize what succeeded and what is missing."
)
# Future-tense "I'm about to do X" phrasing that signals the model stopped instead of acting.
_ACTION_INTENT_RE = re.compile(
    r"\b(?:let me|i'?ll|i will|i'?m going to|i am going to|now let me|let me go ahead|next,? i'?ll)\b"
    r"(?:\s+\w+){0,2}\s+"  # allow a couple of filler words: "let me first see", "let me quickly check"
    r"(?:check|fetch|pull|grab|look|find|search|get|see|read|retrieve|load|open|review|gather|list|scan|"
    r"look up|take a look)"
    r"|\b(?:one moment|hold on|bear with me|just a (?:sec|second|moment)|give me a (?:sec|second|moment))\b",
    re.IGNORECASE,
)


def looks_like_unfinished_action(text: str) -> bool:
    """True when a tool-less reply reads like an announced-but-not-performed action."""
    return bool(_ACTION_INTENT_RE.search(text or ""))


# A tool-less reply that poses a question to the user is *waiting on them*, not stalling on an
# un-called tool. Auto-continuing it just nudges the model into an empty turn (e.g. "What would
# you like me to search for? I'll get started right away!" — there's nothing to search yet).
_USER_QUESTION_RE = re.compile(
    r"\b(?:what|which|who|where|when|why|how|do you|did you|are you|would you|could you|"
    r"can you|should i|shall i|want me to|would you like|let me know|tell me)\b[^?]*\?",
    re.IGNORECASE,
)


def awaiting_user_input(text: str) -> bool:
    """True when a tool-less reply is asking the user a question, so the turn is complete."""
    return bool(_USER_QUESTION_RE.search(text or ""))


_LOST_CONTEXT_RE = re.compile(
    r"\b(?:what would you like me to work on|what can i help you with|how can i help|"
    r"ready to help you with whatever|remind me what(?: task)?|where we left off)\b",
    re.IGNORECASE,
)


def looks_like_lost_context_reply(text: str) -> bool:
    """True when a reply sounds like the model forgot an active tool task."""
    return bool(_LOST_CONTEXT_RE.search(text or ""))


def render_active_user_task_context(messages: List[Dict[str, Any]], max_turns: int = 6, max_chars: int = 1600) -> str:
    user_turns = [
        re.sub(r"\s+", " ", str(message.get("content") or "")).strip()
        for message in messages
        if message.get("role") == "user" and str(message.get("content") or "").strip()
    ][-max_turns:]
    if not user_turns:
        return ""
    lines = ["Recent user turns defining the active task:"]
    lines.extend(f"- {turn}" for turn in user_turns)
    lines.append(
        "If the latest user turn is short or ambiguous, interpret it as continuing these prior turns. "
        "Do not ask what to work on when tool results for this task are already available; synthesize the results."
    )
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def apply_voice_directive(system_prompt: str, payload: "ChatPayload") -> str:
    if getattr(payload, "voice", False):
        return f"{system_prompt}\n\n{VOICE_REPLY_DIRECTIVE}".strip()
    return system_prompt


def apply_bestie_identity(system_prompt: str, settings: Dict[str, Any]) -> str:
    """Overlay the active bestie's identity + personality. Empty/unknown = built-in Sammy."""
    bestie = db.get_bestie(settings.get("active_bestie_id"))
    if not bestie:
        return system_prompt
    identity = f"You are {bestie['name']}, the user's AI companion."
    personality = (bestie.get("personality") or "").strip()
    if personality:
        identity = f"{identity} {personality}"
    return f"{identity}\n\n{system_prompt}".strip()


class ToolCredentialsPayload(BaseModel):
    credentials: Dict[str, Any]


class LoginPayload(BaseModel):
    password: str = ""


class MemoryPayload(BaseModel):
    scope: str = "user"
    agent_id: str = ""
    kind: str = "fact"
    content: str
    status: str = "active"
    confidence: float = 1.0
    sensitive: bool = False
    expires_at: Optional[str] = None


GOOGLE_SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send",
    "google_contacts": "https://www.googleapis.com/auth/contacts",
}

ZOHO_MAIL_SCOPE = "ZohoMail.messages.ALL,ZohoMail.folders.READ,ZohoMail.accounts.READ"


def resolve_model_name(preferred_model: Optional[str], models_data: List[Dict[str, Any]]) -> str:
    names = [str(model.get("name") or "") for model in models_data if model.get("name")]
    if preferred_model and preferred_model in names:
        return preferred_model

    candidates: List[str] = []
    if preferred_model:
        candidates.append(preferred_model.replace("jarvis-", "sammy-", 1))
        candidates.append(preferred_model.replace("jarvis", "sammy"))
        if ":" not in preferred_model:
            candidates.append(f"{preferred_model}:latest")

    for candidate in candidates:
        if candidate in names:
            return candidate
    return names[0] if names else (preferred_model or "")


ZOHO_ACCOUNTS = {
    "us": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "in": "https://accounts.zoho.in",
    "au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
}

ZOHO_MAIL_ACCOUNTS = {
    "com": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "in": "https://accounts.zoho.in",
    "com.au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
    "sa": "https://accounts.zoho.sa",
}


@app.on_event("startup")
async def startup() -> None:
    db.init_db()
    load_auth_sessions()
    migrate_zoho_mail_credentials()
    migrate_plugin_references()
    memory.initialize(db.list_agents())
    auto_enable_plugins()
    auto_enable_spreadsheet_tools()
    await publish_mdns_alias()


@app.on_event("shutdown")
async def shutdown() -> None:
    await unpublish_mdns_alias()


def registry() -> ToolRegistry:
    return ToolRegistry(db.get_tool_credentials)


def access_password_hash() -> str:
    return str(db.get_settings().get("access_password_hash") or "")


def access_password_enabled() -> bool:
    return bool(access_password_hash())


ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
ELEVENLABS_DEFAULT_MODEL = "eleven_turbo_v2_5"
ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" — a calm female default


def elevenlabs_api_key() -> str:
    """ElevenLabs key from the environment (preferred) or the encrypted local setting.

    Never returned to the browser — only used server-side to proxy TTS requests.
    """
    env_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if env_key:
        return env_key
    token = str(db.get_settings().get("elevenlabs_api_key_enc") or "")
    return str(decrypt_json(token).get("key") or "") if token else ""


def gemini_api_key() -> str:
    """Gemini key from the environment (preferred) or the encrypted local setting.

    Never returned to the browser — only used server-side to proxy image generation.
    """
    env_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if env_key:
        return env_key
    token = str(db.get_settings().get("gemini_api_key_enc") or "")
    return str(decrypt_json(token).get("key") or "") if token else ""


def public_settings() -> Dict[str, Any]:
    settings = db.get_settings()
    enabled = bool(settings.pop("access_password_hash", ""))
    settings["access_password_enabled"] = enabled
    # Never expose secret keys to the browser — only whether one is configured.
    settings.pop("elevenlabs_api_key_enc", None)
    settings["elevenlabs_configured"] = bool(elevenlabs_api_key())
    settings.pop("gemini_api_key_enc", None)
    settings["gemini_configured"] = bool(gemini_api_key())
    return settings


def authenticated(request: Request) -> bool:
    if not access_password_enabled():
        return True
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return False
    expires_at = auth_sessions.get(token)
    if not expires_at:
        return False
    if expires_at < time.time():
        auth_sessions.pop(token, None)
        save_auth_sessions()
        return False
    return True


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=AUTH_SESSION_SECONDS,
        httponly=True,
        samesite="lax",
    )


@app.middleware("http")
async def require_access_password(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in AUTH_EXEMPT_PATHS and not authenticated(request):
        return JSONResponse(
            {"detail": "Login required"},
            status_code=401,
            headers={"X-Sammy-Auth-Required": "true"},
        )
    return await call_next(request)


def migrate_zoho_mail_credentials() -> None:
    existing = db.get_tool_credentials("zoho_mail")
    if existing:
        return

    tool = registry().get("zoho_mail")
    if not tool:
        return
    try:
        from tools.zoho_mail_tool import ZohoMailTool

        imported = ZohoMailTool.load_external_credentials()
    except Exception:
        imported = {}
    if imported:
        db.save_tool_credentials("zoho_mail", imported, tool.validate_auth(imported))


def auto_enable_plugins() -> None:
    if db.get_settings().get("plugin_auto_enable_v3"):
        return
    names = set(registry().names())
    defaults = []
    if "zoho_mail" in names:
        defaults.append("zoho_mail")
    if "gmail" in names:
        defaults.append("gmail")
    if defaults:
        db.add_enabled_tools(["default", "email-manager"], defaults)
    db.update_settings({"plugin_auto_enable_v3": True})


def auto_enable_spreadsheet_tools() -> None:
    if db.get_settings().get("spreadsheet_tools_auto_enable_v1"):
        return
    names = set(registry().names())
    defaults = [name for name in ("excel", "numbers") if name in names]
    if defaults:
        db.add_enabled_tools(["default"], defaults)
    db.update_settings({"spreadsheet_tools_auto_enable_v1": True})


def migrate_plugin_references() -> None:
    reg = registry()
    available = set(reg.names())
    aliases = reg.plugin_aliases()
    if "gmail" in available:
        aliases.setdefault("codex_plugin__gmail", "gmail")
    if "github" in available:
        aliases.setdefault("codex_plugin__github", "github")

    for agent in db.list_agents():
        enabled = agent.get("enabled_tools") or []
        migrated: List[str] = []
        changed = False
        for tool_name in enabled:
            next_name = aliases.get(tool_name, tool_name)
            if next_name != tool_name:
                changed = True
            if next_name.startswith("codex_plugin__") and next_name not in available:
                changed = True
                continue
            if next_name not in migrated:
                migrated.append(next_name)
        if changed:
            db.save_agent(
                {
                    "name": agent["name"],
                    "system_prompt": agent.get("system_prompt") or "",
                    "model": agent.get("model") or "",
                    "icon": agent.get("icon") or "",
                    "enabled_tools": migrated,
                },
                agent["id"],
            )


def merge_tool_credentials(tool: Any, incoming: Dict[str, Any]) -> Dict[str, Any]:
    existing = db.get_tool_credentials(tool.name)
    merged = {**existing}
    fields = {field.get("name"): field for field in tool.get_auth_fields()}

    for name, value in incoming.items():
        field = fields.get(name) or {}
        if field.get("type") == "password" and value == "" and existing.get(name):
            continue
        if value == "" and name not in existing:
            continue
        merged[name] = value

    return merged


def sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


TOKEN_LIMIT_FINISH_REASONS = {"length", "max_tokens", "num_predict"}


def assistant_response_metadata(
    reasoning: str = "",
    finish_reason: str = "",
    response_status: str = "",
    response_notice: str = "",
    response_error: str = "",
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"reasoning": reasoning}
    if finish_reason:
        metadata["finish_reason"] = finish_reason
    if response_status:
        metadata["response_status"] = response_status
    if response_notice:
        metadata["response_notice"] = response_notice
    if response_error:
        metadata["response_error"] = response_error
    return metadata


def ollama_message_history(conversation_id: str, system_prompt: str) -> List[Dict[str, Any]]:
    history = [{"role": "system", "content": system_prompt}]
    history.extend(db.list_chat_messages(conversation_id))
    return history


def normalize_tool_calls(raw_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calls = []
    for call in raw_calls or []:
        function = call.get("function") or call
        name = function.get("name")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw": arguments}
        if name:
            calls.append({"name": name, "arguments": arguments})
    return calls


AUTH_RECONNECT_MARKERS = (
    "401 client error",
    "401 unauthorized",
    "http 401",
    "status code 401",
    "unauthorized for url",
    "http 400 from accounts.zoho",
    "invalid_grant",
    "invalid_code",
    "invalid_token",
    "invalid client",
    "invalid_client",
    "invalid_client_secret",
    "invalid oauth",
    "is not connected",
    "not connected. add oauth",
    "token has been expired or revoked",
)


def auth_failure_requires_reconnect(content: str) -> bool:
    lowered = (content or "").lower()
    return any(marker in lowered for marker in AUTH_RECONNECT_MARKERS)


def mark_tool_needs_reconnect(tool_name: str, content: str = "") -> bool:
    tool = registry().get(tool_name)
    if not tool or not getattr(tool, "requires_auth", False):
        return False
    if content and not auth_failure_requires_reconnect(content):
        return False
    db.save_tool_credentials(tool_name, db.get_tool_credentials(tool_name), False)
    return True


def refresh_zoho_oauth_token(tool_name: str) -> Optional[str]:
    credentials = db.get_tool_credentials(tool_name)
    refresh_token = credentials.get("refresh_token")
    client_id = credentials.get("client_id")
    client_secret = credentials.get("client_secret")
    if not refresh_token or not client_id or not client_secret:
        return None

    if tool_name == "zoho_mail":
        dc = credentials.get("dc") or "com"
        accounts = credentials.get("accounts_base") or ZOHO_MAIL_ACCOUNTS.get(dc, ZOHO_MAIL_ACCOUNTS["com"])
    elif tool_name == "zoho_crm":
        region = credentials.get("region") or "us"
        accounts = ZOHO_ACCOUNTS.get(region, ZOHO_ACCOUNTS["us"])
    else:
        return None

    try:
        response = requests.post(
            f"{accounts.rstrip('/')}/oauth/v2/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
    except requests.RequestException:
        return None

    if response.ok:
        merged = {**credentials, **response.json(), "refresh_token": refresh_token}
        tool = registry().get(tool_name)
        connected = tool.validate_auth(merged) if tool else False
        db.save_tool_credentials(tool_name, merged, connected)
        return None

    if response.status_code in {400, 401} and auth_failure_requires_reconnect(response.text):
        mark_tool_needs_reconnect(tool_name)
        tool = registry().get(tool_name)
        display_name = tool.display_name if tool else tool_name
        return f"{display_name} needs reconnecting."
    return None


def refresh_google_oauth_token(tool_name: str) -> Optional[str]:
    if tool_name not in GOOGLE_SCOPES:
        return None
    credentials = db.get_tool_credentials(tool_name)
    refresh_token = credentials.get("refresh_token")
    client_id = credentials.get("client_id")
    client_secret = credentials.get("client_secret")
    if not refresh_token or not client_id or not client_secret:
        return None

    try:
        response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
    except requests.RequestException:
        return None

    if response.ok:
        merged = {**credentials, **response.json(), "refresh_token": refresh_token}
        tool = registry().get(tool_name)
        connected = tool.validate_auth(merged) if tool else False
        db.save_tool_credentials(tool_name, merged, connected)
        return None

    if response.status_code in {400, 401} and auth_failure_requires_reconnect(response.text):
        mark_tool_needs_reconnect(tool_name)
        tool = registry().get(tool_name)
        display_name = tool.display_name if tool else tool_name
        return f"{display_name} needs reconnecting."
    return None


def check_tool_connection(tool_name: str) -> Optional[str]:
    reg = registry()
    tool = reg.get(tool_name)
    if not tool or not getattr(tool, "requires_auth", False):
        return None

    normalized_name = tool.name
    credentials = db.get_tool_credentials(normalized_name)
    if not tool.validate_auth(credentials):
        db.save_tool_credentials(normalized_name, credentials, False)
        return f"{tool.display_name or normalized_name} needs reconnecting."

    notice = None
    if normalized_name in GOOGLE_SCOPES:
        notice = refresh_google_oauth_token(normalized_name)
    elif normalized_name in {"zoho_mail", "zoho_crm"}:
        notice = refresh_zoho_oauth_token(normalized_name)
    if notice:
        return notice

    latest_credentials = db.get_tool_credentials(normalized_name)
    db.save_tool_credentials(normalized_name, latest_credentials, tool.validate_auth(latest_credentials))
    return None


def check_agent_tool_connections(agent: Dict[str, Any]) -> List[str]:
    reg = registry()
    notices: List[str] = []
    checked = set()
    for tool_name in agent.get("enabled_tools") or []:
        normalized = reg.normalize_tool_name(tool_name)
        if normalized in checked:
            continue
        checked.add(normalized)
        notice = check_tool_connection(normalized)
        if notice:
            notices.append(notice)
    return notices


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "ollama_url": OLLAMA_URL}


@app.get("/api/auth/status")
async def auth_status(request: Request) -> Dict[str, bool]:
    password_required = access_password_enabled()
    return {
        "password_required": password_required,
        "authenticated": True if not password_required else authenticated(request),
    }


@app.post("/api/auth/login")
async def auth_login(payload: LoginPayload, response: Response) -> Dict[str, bool]:
    password_hash = access_password_hash()
    if not password_hash:
        return {"password_required": False, "authenticated": True}
    if not verify_password(payload.password, password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")
    token = secrets.token_urlsafe(32)
    auth_sessions[token] = time.time() + AUTH_SESSION_SECONDS
    save_auth_sessions()
    set_auth_cookie(response, token)
    return {"password_required": True, "authenticated": True}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response) -> Dict[str, bool]:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        auth_sessions.pop(token, None)
        save_auth_sessions()
    response.delete_cookie(AUTH_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/models")
async def models() -> Dict[str, Any]:
    return {"models": await list_models()}


def _lan_ip() -> str:
    """Best-effort LAN IPv4 of this Mac (skips loopback/link-local)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
            return ip
    except OSError:
        pass
    return ""


def _local_hostname() -> str:
    """macOS Bonjour name (LocalHostName), resolvable as <name>.local on the LAN."""
    try:
        result = subprocess.run(
            ["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=2
        )
        name = (result.stdout or "").strip()
        if name:
            return name.lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


# Tailscale CLI lives in PATH (open-source/Homebrew installs) or inside the App Store app bundle.
TAILSCALE_BINS = [
    "tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
]


def _tailscale_https_url(bin_path: str) -> str:
    """If `tailscale serve` is exposing Sammy over HTTPS, return that https:// URL (no port)."""
    try:
        result = subprocess.run(
            [bin_path, "serve", "status", "--json"], capture_output=True, text=True, timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return ""
    web = (data or {}).get("Web") or {}
    for host_key, conf in web.items():
        for handler in ((conf or {}).get("Handlers") or {}).values():
            proxy = (handler or {}).get("Proxy") or ""
            if f":{PORT}" in proxy:
                return f"https://{host_key.rsplit(':', 1)[0]}"
    return ""


def _tailscale_info() -> Dict[str, Any]:
    """This Mac's Tailscale MagicDNS name / IP, for reaching Sammy from ANY network.

    Returns empty fields when Tailscale isn't installed or isn't logged in/running.
    """
    for bin_path in TAILSCALE_BINS:
        try:
            result = subprocess.run(
                [bin_path, "status", "--json"], capture_output=True, text=True, timeout=3
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0 or not result.stdout:
            continue
        try:
            data = json.loads(result.stdout)
        except ValueError:
            continue
        self_node = data.get("Self") or {}
        running = data.get("BackendState") == "Running"
        dns = (self_node.get("DNSName") or "").rstrip(".")
        ips = self_node.get("TailscaleIPs") or []
        ipv4 = next((ip for ip in ips if ":" not in ip), "")
        host = dns or ipv4
        return {
            "active": bool(running and host),
            "dns": dns,
            "ip": ipv4,
            "url": f"http://{host}:{PORT}" if host else "",
            "https_url": _tailscale_https_url(bin_path) if dns else "",
        }
    return {"active": False, "dns": "", "ip": "", "url": "", "https_url": ""}


# --- Custom mDNS alias -------------------------------------------------------
# Advertise <alias>.local (default "sammy") on the LAN ourselves, so phones get a stable,
# memorable link WITHOUT the user renaming their Mac's LocalHostName. Lives only while the
# backend runs; best-effort (skips silently if zeroconf is unavailable or there's no LAN IP).
_mdns_zeroconf = None
_mdns_info = None
_mdns_alias = ""


def network_alias() -> str:
    """The custom mDNS alias (without the .local suffix). Empty disables it."""
    env = (os.environ.get("SAMMY_ALIAS") or "").strip()
    if env:
        return env.lower()
    setting = str(db.get_settings().get("network_alias") or "").strip()
    return (setting or "sammy").lower()


async def publish_mdns_alias() -> None:
    # Uses the async zeroconf API because this runs inside uvicorn's event loop (the sync
    # API raises EventLoopBlocked there).
    global _mdns_zeroconf, _mdns_info, _mdns_alias
    alias = network_alias()
    ip = _lan_ip()
    if not alias or not ip:
        return
    try:
        from zeroconf import ServiceInfo
        from zeroconf.asyncio import AsyncZeroconf

        info = ServiceInfo(
            "_http._tcp.local.",
            f"{alias}._http._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=PORT,
            server=f"{alias}.local.",
            properties={},
        )
        azc = AsyncZeroconf()
        await azc.async_register_service(info)
        _mdns_zeroconf, _mdns_info, _mdns_alias = azc, info, alias
    except Exception:  # noqa: BLE001 - alias is a convenience; never block startup
        _mdns_zeroconf = _mdns_info = None
        _mdns_alias = ""


async def unpublish_mdns_alias() -> None:
    global _mdns_zeroconf, _mdns_info, _mdns_alias
    try:
        if _mdns_zeroconf and _mdns_info:
            await _mdns_zeroconf.async_unregister_service(_mdns_info)
            await _mdns_zeroconf.async_close()
    except Exception:  # noqa: BLE001
        pass
    _mdns_zeroconf = _mdns_info = None
    _mdns_alias = ""


@app.get("/api/network")
async def network() -> Dict[str, Any]:
    """Connection details for opening Sammy from a phone on the same Wi-Fi."""
    ip = _lan_ip()
    host = _local_hostname()
    tailscale = await asyncio.to_thread(_tailscale_info)
    return {
        "port": PORT,
        "lan_ip": ip,
        "lan_url": f"http://{ip}:{PORT}" if ip else "",
        "local_hostname": host,
        "local_url": f"http://{host}.local:{PORT}" if host else "",
        "alias": _mdns_alias,
        "alias_url": f"http://{_mdns_alias}.local:{PORT}" if _mdns_alias else "",
        "lan_active": HOST in ("0.0.0.0", "::"),
        "tailscale_active": tailscale["active"],
        "tailscale_url": tailscale["url"],
        "tailscale_https_url": tailscale["https_url"],
    }


class NetworkModePayload(BaseModel):
    lan: bool


@app.post("/api/network/mode")
async def set_network_mode(payload: NetworkModePayload) -> Dict[str, Any]:
    """Toggle phone/LAN access on or off. Spawns the `sammy` CLI to restart with the new bind;
    sessions persist across the restart so the user stays logged in."""
    script = APP_ROOT / "scripts" / "sammy"
    if not script.exists():
        raise HTTPException(status_code=500, detail="The sammy command was not found.")
    command = "lan" if payload.lan else "local"
    env = {**os.environ, "SAMMY_ALLOW_NETWORK": "1"}
    try:
        subprocess.Popen(  # detached so it outlives this process when it restarts us
            [str(script), command],
            cwd=str(APP_ROOT),
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not restart Sammy: {exc}") from exc
    return {"lan": payload.lan, "restarting": True}


@app.get("/api/settings")
async def settings() -> Dict[str, Any]:
    return public_settings()


@app.put("/api/settings")
async def update_settings(payload: Dict[str, Any], response: Response) -> Dict[str, Any]:
    values = dict(payload)
    access_password = values.pop("access_password", None)
    clear_access_password = bool(values.pop("clear_access_password", False))
    values.pop("access_password_enabled", None)
    values.pop("access_password_hash", None)

    if isinstance(access_password, str) and access_password:
        values["access_password_hash"] = hash_password(access_password)
    elif clear_access_password:
        values["access_password_hash"] = ""

    # ElevenLabs API key: store it encrypted, never as a plain setting. The exposed
    # `elevenlabs_configured` flag is computed in public_settings(), so drop it here.
    elevenlabs_key = values.pop("elevenlabs_api_key", None)
    clear_elevenlabs = bool(values.pop("clear_elevenlabs_api_key", False))
    values.pop("elevenlabs_configured", None)
    values.pop("elevenlabs_api_key_enc", None)
    if isinstance(elevenlabs_key, str) and elevenlabs_key.strip():
        values["elevenlabs_api_key_enc"] = encrypt_json({"key": elevenlabs_key.strip()})
    elif clear_elevenlabs:
        values["elevenlabs_api_key_enc"] = ""

    # Gemini API key: same treatment — stored encrypted, exposed only as `gemini_configured`.
    gemini_key = values.pop("gemini_api_key", None)
    clear_gemini = bool(values.pop("clear_gemini_api_key", False))
    values.pop("gemini_configured", None)
    values.pop("gemini_api_key_enc", None)
    if isinstance(gemini_key, str) and gemini_key.strip():
        values["gemini_api_key_enc"] = encrypt_json({"key": gemini_key.strip()})
    elif clear_gemini:
        values["gemini_api_key_enc"] = ""

    db.update_settings(values)
    if isinstance(access_password, str) and access_password:
        token = secrets.token_urlsafe(32)
        auth_sessions[token] = time.time() + AUTH_SESSION_SECONDS
        save_auth_sessions()
        set_auth_cookie(response, token)
    elif clear_access_password:
        auth_sessions.clear()
        save_auth_sessions()
        response.delete_cookie(AUTH_COOKIE_NAME)
    return public_settings()


class TtsPayload(BaseModel):
    text: str
    voice_id: str = ""
    model_id: str = ""


@app.get("/api/tts/voices")
async def tts_voices() -> Dict[str, Any]:
    key = elevenlabs_api_key()
    if not key:
        return {"configured": False, "voices": []}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{ELEVENLABS_API_BASE}/voices",
                headers={"xi-api-key": key},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach ElevenLabs: {exc}") from exc
    if resp.status_code == 401:
        raise HTTPException(status_code=502, detail="ElevenLabs rejected the API key.")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"ElevenLabs error: {resp.text[:300]}")
    voices = [
        {
            "voice_id": voice.get("voice_id"),
            "name": voice.get("name"),
            "category": voice.get("category"),
            "labels": voice.get("labels") or {},
            "preview_url": voice.get("preview_url"),
        }
        for voice in (resp.json().get("voices") or [])
    ]
    return {"configured": True, "voices": voices}


@app.post("/api/tts")
async def tts(payload: TtsPayload) -> Response:
    key = elevenlabs_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="ElevenLabs API key is not configured.")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nothing to speak.")
    voice_id = payload.voice_id or ELEVENLABS_DEFAULT_VOICE
    model_id = payload.model_id or ELEVENLABS_DEFAULT_MODEL
    body = {
        "text": text[:5000],
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}",
                headers={"xi-api-key": key, "accept": "audio/mpeg"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach ElevenLabs: {exc}") from exc
    if resp.status_code == 401:
        raise HTTPException(status_code=502, detail="ElevenLabs rejected the API key.")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"ElevenLabs error: {resp.text[:300]}")
    return Response(content=resp.content, media_type="audio/mpeg")


@app.post("/api/voices/open-settings")
async def open_voice_settings() -> Dict[str, bool]:
    """Open macOS Spoken Content settings so the user can download Premium voices. macOS-only;
    we can't install voices ourselves (privileged), but we can deep-link the right pane."""
    targets = [
        "x-apple.systempreferences:com.apple.preference.universalaccess?SpokenContent",
        "x-apple.systempreferences:com.apple.preference.universalaccess",
        "x-apple.systempreferences:",
    ]
    for url in targets:
        try:
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        except OSError:
            continue
    raise HTTPException(status_code=500, detail="Could not open System Settings (macOS only).")


@app.get("/api/memories")
async def list_memories(
    scope: Optional[str] = None,
    status: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "memories": db.list_memories(scope=scope, status=status, agent_id=agent_id),
        "stats": db.memory_stats(),
    }


@app.post("/api/memories")
async def create_memory(payload: MemoryPayload) -> Dict[str, Any]:
    try:
        entry = db.add_memory({**payload.dict(), "source_label": "Added manually"})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    memory.sync_memory_files()
    return entry


@app.patch("/api/memories/{memory_id}")
async def patch_memory(memory_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        entry = db.update_memory(memory_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    memory.sync_memory_files()
    return entry


@app.post("/api/memories/{memory_id}/approve")
async def approve_memory(memory_id: str) -> Dict[str, Any]:
    try:
        entry = db.update_memory(memory_id, {"status": "active"})
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    memory.sync_memory_files()
    return entry


@app.delete("/api/memories/{memory_id}")
async def remove_memory(memory_id: str) -> Dict[str, Any]:
    try:
        db.delete_memory(memory_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    memory.sync_memory_files()
    return {"ok": True}


@app.post("/api/memories/consolidate")
async def consolidate_memories() -> Dict[str, int]:
    return memory.consolidate()


@app.get("/api/agents")
async def agents() -> Dict[str, Any]:
    return {"agents": db.list_agents()}


@app.post("/api/agents")
async def create_agent(payload: AgentPayload) -> Dict[str, Any]:
    return db.save_agent(payload.dict())


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, payload: AgentPayload) -> Dict[str, Any]:
    return db.save_agent(payload.dict(), agent_id=agent_id)


@app.delete("/api/agents/{agent_id}")
async def remove_agent(agent_id: str) -> Dict[str, Any]:
    db.delete_agent(agent_id)
    return {"ok": True}


GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_STYLIZE_PROMPT = (
    "Turn the main subject of this photo into a cute, round mascot logo drawn in the same style "
    "as a fluffy white Samoyed dog cartoon: soft rounded shapes, big friendly smile, simple warm "
    "peach/cream background, centered, clean illustration suitable for an app icon. Keep the "
    "recognizable features of the original subject so it still looks like them."
)


def _store_image_upload(data: bytes, content_type: str, filename: str) -> Dict[str, Any]:
    """Persist raw image bytes through the same upload store used by /api/files."""
    upload_id = db.new_id()
    safe_name = Path(filename or "bestie.png").name
    target = UPLOAD_DIR / f"{upload_id}-{safe_name}"
    target.write_bytes(data)
    return db.save_upload(upload_id, safe_name, str(target), content_type or "image/png", len(data))


@app.get("/api/besties")
async def besties() -> Dict[str, Any]:
    return {"besties": db.list_besties()}


@app.post("/api/besties")
async def create_bestie(payload: BestiePayload) -> Dict[str, Any]:
    return db.save_bestie(payload.dict())


@app.put("/api/besties/{bestie_id}")
async def update_bestie(bestie_id: str, payload: BestiePayload) -> Dict[str, Any]:
    return db.save_bestie(payload.dict(), bestie_id=bestie_id)


@app.delete("/api/besties/{bestie_id}")
async def remove_bestie(bestie_id: str) -> Dict[str, Any]:
    db.delete_bestie(bestie_id)
    # If the deleted bestie was active, fall back to the built-in Sammy identity.
    if str(db.get_settings().get("active_bestie_id") or "") == bestie_id:
        db.update_settings({"active_bestie_id": ""})
    return {"ok": True}


@app.post("/api/bestie/stylize")
async def stylize_bestie(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Restyle an uploaded photo into a Sammy-style mascot via Gemini.

    Falls back to storing the original photo when no Gemini key is set or the call fails,
    so the bestie flow always yields a usable avatar file id.
    """
    content = await file.read()
    content_type = file.content_type or "image/png"
    key = gemini_api_key()
    if key:
        try:
            request_body = {
                "contents": [
                    {
                        "parts": [
                            {"text": GEMINI_STYLIZE_PROMPT},
                            {"inlineData": {"mimeType": content_type, "data": base64.b64encode(content).decode()}},
                        ]
                    }
                ]
            }
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json=request_body,
                )
            if resp.status_code < 400:
                parts = (
                    resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                )
                for part in parts:
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        image_bytes = base64.b64decode(inline["data"])
                        mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                        saved = _store_image_upload(image_bytes, mime, "bestie.png")
                        return {"file_id": saved["id"], "stylized": True}
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            pass
    saved = _store_image_upload(content, content_type, file.filename or "bestie.png")
    return {"file_id": saved["id"], "stylized": False}


@app.get("/api/conversations")
async def conversations() -> Dict[str, Any]:
    return {"conversations": db.list_conversations()}


@app.post("/api/conversations")
async def create_conversation(payload: ConversationCreate) -> Dict[str, Any]:
    models_data = await list_models()
    model = resolve_model_name(payload.model, models_data)
    agent = db.get_agent(payload.agent_id)
    conversation = db.create_conversation(payload.title, model, agent["id"], payload.mode)
    check_agent_tool_connections(agent)
    return conversation


@app.get("/api/conversations/{conversation_id}")
async def conversation(conversation_id: str) -> Dict[str, Any]:
    try:
        return db.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.patch("/api/conversations/{conversation_id}")
async def patch_conversation(conversation_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return db.update_conversation(conversation_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")


@app.delete("/api/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str) -> Dict[str, Any]:
    db.delete_conversation(conversation_id)
    return {"ok": True}


@app.get("/api/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str) -> PlainTextResponse:
    try:
        data = db.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Conversation not found")
    lines = [f"# {data['conversation']['title']}", ""]
    for message in data["messages"]:
        role = message["role"].title()
        if role == "Tool":
            meta = message.get("metadata") or {}
            role = f"Tool: {meta.get('function_name', 'tool')}"
        lines.extend([f"## {role}", "", message["content"], ""])
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@app.get("/api/tools")
async def tools() -> Dict[str, Any]:
    return {"tools": registry().list_tools(db.get_tool_statuses())}


@app.get("/api/plugins")
async def plugins() -> Dict[str, Any]:
    return {"plugins": registry().list_tools(db.get_tool_statuses())}


@app.get("/api/codex-plugins")
async def codex_plugins() -> Dict[str, Any]:
    return {"plugins": registry().codex_plugin_metadata()}


@app.get("/api/plugin-bundles")
async def plugin_bundles() -> Dict[str, Any]:
    return {"plugins": registry().codex_plugin_metadata()}


@app.put("/api/tools/{tool_name}/credentials")
async def save_tool_credentials(tool_name: str, payload: ToolCredentialsPayload) -> Dict[str, Any]:
    tool = registry().get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    credentials = merge_tool_credentials(tool, payload.credentials)
    connected = tool.validate_auth(credentials)
    db.save_tool_credentials(tool_name, credentials, connected)
    return {"ok": True, "connected": connected}


@app.put("/api/plugins/{plugin_name}/credentials")
async def save_plugin_credentials(plugin_name: str, payload: ToolCredentialsPayload) -> Dict[str, Any]:
    return await save_tool_credentials(plugin_name, payload)


@app.post("/api/tools/{tool_name}/test")
async def test_tool(tool_name: str) -> Dict[str, Any]:
    tool = registry().get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    credentials = db.get_tool_credentials(tool_name)
    return {"connected": tool.validate_auth(credentials)}


@app.post("/api/plugins/{plugin_name}/test")
async def test_plugin(plugin_name: str) -> Dict[str, Any]:
    return await test_tool(plugin_name)


@app.get("/api/tools/{tool_name}/oauth/start")
async def oauth_start(tool_name: str, redirect_uri_override: str = ""):
    tool = registry().get(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    credentials = db.get_tool_credentials(tool_name)
    client_id = credentials.get("client_id")
    client_secret = credentials.get("client_secret")
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Save Client ID and Client Secret first.")

    state = secrets.token_urlsafe(24)
    db.save_oauth_state(state, tool_name)
    redirect_uri = redirect_uri_override or OAUTH_REDIRECT_URI

    if tool_name in GOOGLE_SCOPES:
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": GOOGLE_SCOPES[tool_name],
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
        return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{query}")

    if tool_name == "zoho_crm":
        region = credentials.get("region") or "us"
        accounts = ZOHO_ACCOUNTS.get(region, ZOHO_ACCOUNTS["us"])
        query = urlencode(
            {
                "scope": "ZohoCRM.modules.ALL",
                "client_id": client_id,
                "response_type": "code",
                "access_type": "offline",
                "prompt": "consent",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return RedirectResponse(f"{accounts}/oauth/v2/auth?{query}")

    if tool_name == "zoho_mail":
        dc = credentials.get("dc") or "com"
        accounts = ZOHO_MAIL_ACCOUNTS.get(dc, ZOHO_MAIL_ACCOUNTS["com"])
        query = urlencode(
            {
                "scope": ZOHO_MAIL_SCOPE,
                "client_id": client_id,
                "response_type": "code",
                "access_type": "offline",
                "prompt": "consent",
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return RedirectResponse(f"{accounts}/oauth/v2/auth?{query}")

    raise HTTPException(status_code=400, detail="This tool does not support browser OAuth.")


@app.get("/api/plugins/{plugin_name}/oauth/start")
async def plugin_oauth_start(plugin_name: str):
    return await oauth_start(plugin_name, OAUTH_REDIRECT_URI)


async def finish_oauth_callback(
    tool_name: str,
    code: str = "",
    error: str = "",
    redirect_uri: str = OAUTH_REDIRECT_URI,
):
    if error:
        return PlainTextResponse(f"Sammy OAuth error: {error}", status_code=400)

    credentials = db.get_tool_credentials(tool_name)
    token_data: Dict[str, Any]

    try:
        if tool_name in GOOGLE_SCOPES:
            response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": credentials["client_id"],
                    "client_secret": credentials["client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=20,
            )
            response.raise_for_status()
            token_data = response.json()
        elif tool_name == "zoho_crm":
            region = credentials.get("region") or "us"
            accounts = ZOHO_ACCOUNTS.get(region, ZOHO_ACCOUNTS["us"])
            response = requests.post(
                f"{accounts}/oauth/v2/token",
                data={
                    "code": code,
                    "client_id": credentials["client_id"],
                    "client_secret": credentials["client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=20,
            )
            response.raise_for_status()
            token_data = response.json()
        elif tool_name == "zoho_mail":
            dc = credentials.get("dc") or "com"
            accounts = ZOHO_MAIL_ACCOUNTS.get(dc, ZOHO_MAIL_ACCOUNTS["com"])
            response = requests.post(
                f"{accounts}/oauth/v2/token",
                data={
                    "code": code,
                    "client_id": credentials["client_id"],
                    "client_secret": credentials["client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=20,
            )
            response.raise_for_status()
            token_data = response.json()
        else:
            raise RuntimeError("Unsupported OAuth tool.")
    except Exception as exc:
        return PlainTextResponse(f"Sammy OAuth token exchange failed: {exc}", status_code=400)

    merged = {**credentials, **token_data}
    tool = registry().get(tool_name)
    connected = tool.validate_auth(merged) if tool else False
    db.save_tool_credentials(tool_name, merged, connected)
    title = escape(tool.display_name if tool else tool_name)
    html = f"""
    <!doctype html>
    <html>
      <head><title>Sammy OAuth</title></head>
      <body style="font-family: system-ui; background:#0f0e0b; color:#f4eadc; padding:32px;">
        <h1>{title} connected</h1>
        <p>You can close this tab and return to Sammy.</p>
        <script>setTimeout(() => window.close(), 1200);</script>
      </body>
    </html>
    """
    return PlainTextResponse(html, media_type="text/html")


@app.get("/api/oauth/callback")
async def shared_oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return PlainTextResponse(f"Sammy OAuth error: {error}", status_code=400)
    state_tool = db.pop_oauth_state(state)
    if not state_tool:
        return PlainTextResponse("Sammy OAuth state mismatch.", status_code=400)
    return await finish_oauth_callback(state_tool, code, error, OAUTH_REDIRECT_URI)


@app.get("/api/tools/{tool_name}/oauth/callback")
async def oauth_callback(
    tool_name: str,
    code: str = "",
    state: str = "",
    error: str = "",
    redirect_uri_override: str = "",
):
    if error:
        return PlainTextResponse(f"Sammy OAuth error: {error}", status_code=400)
    state_tool = db.pop_oauth_state(state)
    if state_tool != tool_name:
        return PlainTextResponse("Sammy OAuth state mismatch.", status_code=400)
    redirect_uri = redirect_uri_override or f"{PUBLIC_URL}/api/tools/{tool_name}/oauth/callback"
    return await finish_oauth_callback(tool_name, code, error, redirect_uri)


@app.get("/api/plugins/{plugin_name}/oauth/callback")
async def plugin_oauth_callback(plugin_name: str, code: str = "", state: str = "", error: str = ""):
    if error:
        return PlainTextResponse(f"Sammy OAuth error: {error}", status_code=400)
    state_tool = db.pop_oauth_state(state)
    if state_tool != plugin_name:
        return PlainTextResponse("Sammy OAuth state mismatch.", status_code=400)
    redirect_uri = f"{PUBLIC_URL}/api/plugins/{plugin_name}/oauth/callback"
    return await finish_oauth_callback(plugin_name, code, error, redirect_uri)


@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    upload_id = db.new_id()
    safe_name = Path(file.filename or "upload").name
    target = UPLOAD_DIR / f"{upload_id}-{safe_name}"
    content = await file.read()
    target.write_bytes(content)
    return db.save_upload(upload_id, safe_name, str(target), file.content_type or "", len(content))


@app.get("/api/files/{upload_id}")
async def get_uploaded_file(upload_id: str) -> FileResponse:
    upload = db.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(upload["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type=upload.get("content_type") or "application/octet-stream")


@app.post("/api/chat/stream")
async def stream_chat(payload: ChatPayload) -> StreamingResponse:
    settings = db.get_settings()
    if payload.conversation_id:
        conversation = db.get_conversation(payload.conversation_id)["conversation"]
        agent = db.get_agent(conversation.get("agent_id"))
    else:
        agent = db.get_agent(payload.agent_id)

    model = payload.model or agent.get("model") or settings.get("default_model")
    models_data = await list_models()
    model = resolve_model_name(model, models_data)
    if not model:
        raise HTTPException(status_code=400, detail="No Ollama model available. Install one with `ollama pull`.")

    if not payload.conversation_id:
        conversation = db.create_conversation("New chat", model, agent["id"])
        check_agent_tool_connections(agent)

    conversation_id = conversation["id"]
    db.update_conversation(conversation_id, {"model": model, "agent_id": agent["id"]})

    is_regeneration = bool(payload.regenerate_from)
    if is_regeneration:
        db.replace_messages_after(conversation_id, payload.regenerate_from)

    message_text = payload.message
    if payload.attachments:
        attachment_notes = []
        for upload_id in payload.attachments:
            upload = db.get_upload(upload_id)
            if not upload:
                continue
            attachment_notes.append(f"Attached file: {upload['filename']} ({upload['path']})")
        if attachment_notes:
            message_text = f"{message_text}\n\n" + "\n".join(attachment_notes)

    user_message = None if is_regeneration else db.add_message(conversation_id, "user", message_text)

    async def generate():
        yield sse("conversation", {"conversation": db.get_conversation(conversation_id)["conversation"]})
        if user_message:
            yield sse("message", user_message)

        reg = registry()
        enabled_tools = agent.get("enabled_tools") or []
        plugin_tool_definitions = reg.tool_definitions(enabled_tools)
        tool_definitions = (
            plugin_tool_definitions
            + memory.memory_tool_definitions()
            + plugin_builder.tool_definitions()
        )
        options = {
            "num_ctx": int(payload.options.get("num_ctx") or settings.get("num_ctx") or 8192),
            "num_predict": int(payload.options.get("num_predict") or settings.get("num_predict") or 2048),
            "temperature": float(payload.options.get("temperature") or settings.get("temperature") or 0.2),
            "think": bool(payload.options.get("think", settings.get("think", False))),
        }

        system_prompt = agent.get("system_prompt") or settings.get("system_prompt") or ""
        system_prompt = f"{system_prompt}\n\n{memory.memory_context(agent, payload.message, conversation_id)}".strip()
        build_mode = conversation.get("mode") == "tool_builder"
        system_prompt = f"{system_prompt}\n\n{plugin_builder.context(conversation_id, build_mode=build_mode)}".strip()
        if plugin_tool_definitions:
            system_prompt = (
                f"{system_prompt}\n\n"
                "Enabled tool functions are available in this chat. When the user asks you to use an enabled "
                "tool or integration, call the relevant tool function instead of saying you do not have access. "
                "Connector-only tools may provide skills and context only; when Sammy exposes a native bridge for "
                "one of them, use the bridged Sammy tool functions for the actual action. "
                "For email, use the configured email tool to draft, inspect, or send mail; when replying to an "
                "existing email, use the tool's reply/thread-aware function instead of sending a new message. "
                "Only send after the user has provided or approved the recipient, subject, and body."
            ).strip()
        if plugin_tool_definitions:
            system_prompt = f"{system_prompt}\n\n{AGENTIC_TOOL_DIRECTIVE}".strip()
        plugin_injections = reg.plugin_injections(enabled_tools)
        if plugin_injections:
            system_prompt = f"{system_prompt}\n\n{plugin_injections}".strip()
        system_prompt = apply_bestie_identity(system_prompt, settings)
        system_prompt = apply_voice_directive(system_prompt, payload)
        rounds = 0
        final_answer = ""
        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            messages = ollama_message_history(conversation_id, system_prompt)
            assistant_content = ""
            reasoning_content = ""
            finish_reason = ""
            stream_error = ""
            collected_calls: List[Dict[str, Any]] = []

            try:
                async for chunk in chat_stream(model, messages, tool_definitions, options):
                    if chunk.get("error"):
                        stream_error = str(chunk["error"])
                        yield sse("error", {"message": stream_error})
                        continue
                    message = chunk.get("message") or {}
                    content = message.get("content") or ""
                    thinking = message.get("thinking") or ""
                    if thinking:
                        reasoning_content += thinking
                        yield sse("reasoning", {"content": thinking})
                    if content:
                        assistant_content += content
                        final_answer += content
                        yield sse("token", {"content": content})
                    if message.get("tool_calls"):
                        collected_calls.extend(normalize_tool_calls(message.get("tool_calls") or []))
                    if chunk.get("done"):
                        done_reason = str(chunk.get("done_reason") or "")
                        if done_reason == "load":
                            yield sse("status", {"message": "Model loaded."})
                        elif done_reason:
                            finish_reason = done_reason
            except Exception as exc:
                stream_error = f"Ollama error: {exc}"
                metadata = assistant_response_metadata(
                    reasoning_content,
                    finish_reason,
                    "error",
                    "Sammy hit an Ollama error before finishing this reply.",
                    stream_error,
                )
                saved = db.add_message(conversation_id, "assistant", assistant_content, metadata)
                yield sse("error", {"message": stream_error})
                yield sse("assistant_message", saved)
                yield sse("done", {"message": saved})
                return

            if collected_calls:
                if assistant_content:
                    yield sse("progress_note", {"content": assistant_content})
                function_map = reg.function_map(enabled_tools)
                for call in collected_calls:
                    if memory.is_memory_call(call["name"]):
                        result = memory.save_memory_from_call(agent, call["arguments"])
                        db.add_message(
                            conversation_id,
                            "tool",
                            result["content"],
                            {
                                "tool_name": "sammy_memory",
                                "tool_display_name": "Memory",
                                "function_name": memory.MEMORY_FUNCTION_NAME,
                                "memory_file": result["file_name"],
                            },
                        )
                        if result.get("ok"):
                            yield sse(
                                "memory_save",
                                {
                                    "name": memory.MEMORY_FUNCTION_NAME,
                                    "memory_file": result["file_name"],
                                    "content": result["content"],
                                },
                            )
                        else:
                            yield sse(
                                "tool_result",
                                {
                                    "name": memory.MEMORY_FUNCTION_NAME,
                                    "tool": "sammy_memory",
                                    "tool_display_name": "Memory",
                                    "content": result["content"],
                                },
                            )
                        continue

                    if plugin_builder.is_builder_call(call["name"]):
                        result = plugin_builder.handle_call(
                            conversation_id,
                            agent,
                            payload.message,
                            call["name"],
                            call["arguments"],
                            build_mode=build_mode,
                        )
                        db.add_message(
                            conversation_id,
                            "tool",
                            result["content"],
                            {
                                "tool_name": plugin_builder.BUILDER_TOOL_NAME,
                                "tool_display_name": "Tool Builder",
                                "function_name": call["name"],
                                "proposal_id": result.get("proposal_id"),
                                "proposal_status": result.get("status"),
                            },
                        )
                        yield sse(
                            "tool_result",
                            {
                                "name": call["name"],
                                "tool": plugin_builder.BUILDER_TOOL_NAME,
                                "tool_display_name": "Tool Builder",
                                "content": result["content"],
                                "proposal_id": result.get("proposal_id"),
                                "proposal_status": result.get("status"),
                                "plugin_name": result.get("plugin_name"),
                            },
                        )
                        continue

                    mapped_tool, mapped_name = function_map.get(call["name"], (None, call["name"]))
                    yield sse(
                        "tool_start",
                        {
                            "name": mapped_name,
                            "tool": mapped_tool.name if mapped_tool else "",
                            "tool_display_name": (mapped_tool.display_name or mapped_tool.name) if mapped_tool else "",
                            "arguments": call["arguments"],
                        },
                    )
                    if mapped_tool:
                        refresh_notice = check_tool_connection(mapped_tool.name)
                        if refresh_notice:
                            yield sse("status", {"message": refresh_notice})
                    result = reg.execute(call["name"], call["arguments"], enabled_tools)
                    requires_reconnect = mark_tool_needs_reconnect(result["tool_name"], result["content"])
                    tool_message = db.add_message(
                        conversation_id,
                        "tool",
                        result["content"],
                        {
                            "tool_name": result["tool_name"],
                            "tool_display_name": result.get("tool_display_name"),
                            "function_name": result["function_name"],
                        },
                    )
                    yield sse(
                        "tool_result",
                        {
                            "name": result["function_name"],
                            "tool": result["tool_name"],
                            "tool_display_name": result.get("tool_display_name"),
                            "content": result["content"],
                            "requires_reconnect": requires_reconnect,
                            "message": tool_message,
                        },
                    )
                    if requires_reconnect:
                        display_name = result.get("tool_display_name") or result["tool_name"]
                        yield sse("status", {"message": f"{display_name} needs reconnecting."})
                final_answer = ""
                continue

            response_status = ""
            response_notice = ""
            if stream_error:
                response_status = "error"
                response_notice = "Sammy received an error from the model stream before the reply finished."
            elif finish_reason in TOKEN_LIMIT_FINISH_REASONS:
                response_status = "partial"
                response_notice = "The model reached Sammy's max token setting, so this reply may be cut off."
            elif not assistant_content and reasoning_content:
                response_status = "empty"
                response_notice = "The model produced reasoning but no final reply. Increase max tokens or turn off Reasoning Mode."

            saved = db.add_message(
                conversation_id,
                "assistant",
                assistant_content,
                assistant_response_metadata(
                    reasoning_content,
                    finish_reason,
                    response_status,
                    response_notice,
                    stream_error,
                ),
            )
            yield sse("assistant_message", saved)
            yield sse("done", {"message": saved})
            return

        saved = db.add_message(
            conversation_id,
            "assistant",
            final_answer or "I stopped because the tool call loop reached Sammy's safety limit.",
            assistant_response_metadata(
                response_status="partial",
                response_notice="Sammy stopped after several tool rounds to avoid an endless tool loop.",
            ),
        )
        yield sse("assistant_message", saved)
        yield sse("done", {"message": saved})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        background=BackgroundTask(lambda: None),
    )


CONTINUATION_PROMPT = (
    "Continue the same answer exactly where it stopped. Do not repeat earlier text, do not restart, and do not "
    "mention token limits. Finish every remaining part of the user's task before concluding."
)
COMPACTION_PROMPT = (
    "Compact the older conversation into durable working context. Preserve the user's active goal, constraints, "
    "decisions, exact names/paths/IDs, completed work, pending steps, tool results, and errors. Do not solve the "
    "task or add advice. Return only the compact context."
)
MAX_REPEATED_TOOL_CALLS = 2
MAX_CONSECUTIVE_TOOL_FAILURES = 3
MAX_TOOL_STEPS_EMERGENCY = 64
MAX_STALLED_CONTINUATIONS = 2


async def compact_messages_for_job(
    job: ChatJob,
    model: str,
    messages: List[Dict[str, Any]],
    tool_definitions: List[Dict[str, Any]],
    options: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], bool]:
    num_ctx = int(options.get("num_ctx") or 8192)
    num_predict = int(options.get("num_predict") or 2048)
    budget = context_input_budget(num_ctx, num_predict, tool_definitions)
    estimated = estimate_messages_tokens(messages)
    if estimated <= budget:
        return messages, False

    working_messages = [dict(message) for message in messages]
    if (
        len(working_messages) >= 2
        and working_messages[-2].get("role") == "assistant"
        and working_messages[-1].get("content") == CONTINUATION_PROMPT
    ):
        partial_answer = str(working_messages[-2].get("content") or "")
        tail_chars = max(1600, int(budget * 0.4) * 4)
        if len(partial_answer) > tail_chars:
            working_messages[-2]["content"] = (
                "[Earlier response text omitted during context compaction. Continue from this exact tail:]\n"
                + partial_answer[-tail_chars:]
            )

    await job.set_work_state(
        "compacting",
        "Compacting context",
        f"Preserving the active task while freeing room for the next {num_predict} tokens.",
    )
    system_messages, older, recent = split_history_for_compaction(working_messages, max(512, int(budget * 0.55)))
    if not older and len(recent) > 2:
        older, recent = recent[:-2], recent[-2:]

    source = render_compaction_source(older, max_chars=max(2400, budget * 3))
    summary = ""
    if source:
        summary_options = {
            "num_ctx": max(2048, min(num_ctx, 8192)),
            "num_predict": max(256, min(768, num_predict // 2)),
            "temperature": 0.1,
            "think": False,
        }
        try:
            async for chunk in chat_stream(
                model,
                [
                    {"role": "system", "content": COMPACTION_PROMPT},
                    {"role": "user", "content": source},
                ],
                [],
                dict(summary_options),
            ):
                if job.stop_requested:
                    raise asyncio.CancelledError
                if chunk.get("error"):
                    raise RuntimeError(str(chunk["error"]))
                summary += str((chunk.get("message") or {}).get("content") or "")
        except asyncio.CancelledError:
            raise
        except Exception:
            summary = source

    summary = summary.strip() or source.strip()
    active_task_context = render_active_user_task_context(working_messages)

    def build_compacted_messages() -> List[Dict[str, Any]]:
        compacted_messages = list(system_messages[:1])
        if summary:
            compacted_messages.append({"role": "system", "content": f"Earlier conversation compacted by Sammy:\n{summary}"})
        if active_task_context:
            compacted_messages.append({"role": "system", "content": active_task_context})
        compacted_messages.extend(recent)
        return compacted_messages

    compacted = build_compacted_messages()

    while estimate_messages_tokens(compacted) > budget and len(recent) > 2:
        recent.pop(0)
        compacted = build_compacted_messages()

    if estimate_messages_tokens(compacted) > budget and summary:
        allowed_chars = max(800, budget * 2)
        summary = summary[-allowed_chars:]
        compacted = build_compacted_messages()

    return compacted, True


def background_job_metadata(
    job: ChatJob,
    reasoning: str,
    finish_reason: str,
    *,
    response_status: str = "",
    response_notice: str = "",
    response_error: str = "",
    continuation_parts: int = 1,
    context_compactions: int = 0,
) -> Dict[str, Any]:
    metadata = assistant_response_metadata(
        reasoning,
        finish_reason,
        response_status,
        response_notice,
        response_error,
    )
    metadata.update(
        {
            "job_id": job.id,
            "continuation_parts": continuation_parts,
            "context_compactions": context_compactions,
            "tool_steps": job.tool_step,
        }
    )
    return metadata


async def run_background_chat_job(
    job: ChatJob,
    payload: ChatPayload,
    settings: Dict[str, Any],
    agent: Dict[str, Any],
    conversation: Dict[str, Any],
    user_message: Optional[Dict[str, Any]],
) -> None:
    answer = ""
    reasoning = ""
    finish_reason = ""
    continuation_part = 1
    context_compactions = 0
    stalled_continuations = 0
    empty_attempts = 0
    consecutive_tool_failures = 0
    auto_continues = 0
    last_call_signature = ""
    repeated_call_count = 0
    pending_continue_prompt = ""
    lost_context_retries = 0

    try:
        job.status = "running"
        await job.publish("job", {"job": job.snapshot()})
        await job.publish("conversation", {"conversation": conversation})
        if user_message:
            await job.publish("message", user_message)

        reg = registry()
        enabled_tools = agent.get("enabled_tools") or []
        plugin_tool_definitions = reg.tool_definitions(enabled_tools)
        tool_definitions = (
            plugin_tool_definitions
            + memory.memory_tool_definitions()
            + plugin_builder.tool_definitions()
        )
        options = {
            "num_ctx": int(payload.options.get("num_ctx") or settings.get("num_ctx") or 8192),
            "num_predict": int(payload.options.get("num_predict") or settings.get("num_predict") or 2048),
            "temperature": float(payload.options.get("temperature") or settings.get("temperature") or 0.2),
            "think": bool(payload.options.get("think", settings.get("think", False))),
        }

        system_prompt = agent.get("system_prompt") or settings.get("system_prompt") or ""
        system_prompt = f"{system_prompt}\n\n{memory.memory_context(agent, payload.message, job.conversation_id)}".strip()
        build_mode = conversation.get("mode") == "tool_builder"
        system_prompt = f"{system_prompt}\n\n{plugin_builder.context(job.conversation_id, build_mode=build_mode)}".strip()
        if plugin_tool_definitions:
            system_prompt = (
                f"{system_prompt}\n\n"
                "Enabled tool functions are available in this chat. When the user asks you to use an enabled "
                "tool or integration, call the relevant tool function instead of saying you do not have access. "
                "Continue working through all required tool steps until the task is complete. For email, when "
                "replying to an existing email, use the tool's reply/thread-aware function instead of sending a "
                "new message. Only send after the user has provided or approved the recipient, subject, and body."
            ).strip()
        if plugin_tool_definitions:
            system_prompt = f"{system_prompt}\n\n{AGENTIC_TOOL_DIRECTIVE}".strip()
        plugin_injections = reg.plugin_injections(enabled_tools)
        if plugin_injections:
            system_prompt = f"{system_prompt}\n\n{plugin_injections}".strip()
        system_prompt = apply_bestie_identity(system_prompt, settings)
        system_prompt = apply_voice_directive(system_prompt, payload)

        while True:
            if job.stop_requested:
                raise asyncio.CancelledError

            phase = "writing" if continuation_part == 1 else "continuing"
            label = "Writing response" if continuation_part == 1 else f"Continuing response · part {continuation_part}"
            detail = "" if continuation_part == 1 else "The previous model call reached its output limit."
            await job.set_work_state(phase, label, detail, part=continuation_part)

            messages = ollama_message_history(job.conversation_id, system_prompt)
            if answer:
                messages.extend(
                    [
                        {"role": "assistant", "content": answer},
                        {"role": "user", "content": CONTINUATION_PROMPT},
                    ]
                )
            elif pending_continue_prompt:
                messages.append({"role": "user", "content": pending_continue_prompt})
                pending_continue_prompt = ""
            elif empty_attempts:
                messages.append(
                    {
                        "role": "user",
                        "content": EMPTY_RESPONSE_NUDGE,
                    }
                )

            messages, compacted = await compact_messages_for_job(
                job,
                job.model,
                messages,
                tool_definitions,
                options,
            )
            if compacted:
                context_compactions += 1
                await job.set_work_state(phase, label, "Context compacted; continuing with the active task.")

            segment_content = ""
            segment_reasoning = ""
            collected_calls: List[Dict[str, Any]] = []
            finish_reason = ""
            prefix_buffer = ""
            prefix_resolved = continuation_part == 1 or not answer

            async for chunk in chat_stream(job.model, messages, tool_definitions, dict(options)):
                if job.stop_requested:
                    raise asyncio.CancelledError
                if chunk.get("error"):
                    raise RuntimeError(str(chunk["error"]))

                message = chunk.get("message") or {}
                thinking = str(message.get("thinking") or "")
                if thinking:
                    reasoning += thinking
                    segment_reasoning += thinking
                    await job.publish("reasoning", {"content": thinking})

                content = str(message.get("content") or "")
                if content:
                    if prefix_resolved:
                        answer += content
                        segment_content += content
                        await job.publish("token", {"content": content})
                    else:
                        prefix_buffer += content
                        if len(prefix_buffer) >= 256:
                            cleaned = strip_continuation_overlap(answer, prefix_buffer)
                            answer += cleaned
                            segment_content += cleaned
                            prefix_resolved = True
                            if cleaned:
                                await job.publish("token", {"content": cleaned})

                if message.get("tool_calls"):
                    collected_calls.extend(normalize_tool_calls(message.get("tool_calls") or []))
                if chunk.get("done"):
                    done_reason = str(chunk.get("done_reason") or "")
                    if done_reason == "load":
                        await job.publish("status", {"message": "Model loaded."})
                    elif done_reason:
                        finish_reason = done_reason

            if not prefix_resolved and prefix_buffer:
                cleaned = strip_continuation_overlap(answer, prefix_buffer)
                answer += cleaned
                segment_content += cleaned
                if cleaned:
                    await job.publish("token", {"content": cleaned})

            if collected_calls:
                if answer:
                    await job.publish("progress_note", {"content": answer})
                answer = ""
                reasoning = ""
                continuation_part = 1
                function_map = reg.function_map(enabled_tools)

                for call in collected_calls:
                    if job.stop_requested:
                        raise asyncio.CancelledError
                    signature = tool_call_signature(call["name"], call["arguments"])
                    if signature == last_call_signature:
                        repeated_call_count += 1
                    else:
                        last_call_signature = signature
                        repeated_call_count = 1
                    if repeated_call_count > MAX_REPEATED_TOOL_CALLS:
                        raise RuntimeError(
                            f"Sammy stopped a repeated tool call ({call['name']}) that was no longer making progress."
                        )

                    job.tool_step += 1
                    if job.tool_step > MAX_TOOL_STEPS_EMERGENCY:
                        raise RuntimeError("Sammy reached the emergency 64-step tool guard.")

                    if memory.is_memory_call(call["name"]):
                        await job.set_work_state(
                            "tool",
                            "Saving memory",
                            f"Tool step {job.tool_step}",
                            tool_step=job.tool_step,
                        )
                        result = memory.save_memory_from_call(agent, call["arguments"])
                        db.add_message(
                            job.conversation_id,
                            "tool",
                            result["content"],
                            {
                                "tool_name": "sammy_memory",
                                "tool_display_name": "Memory",
                                "function_name": memory.MEMORY_FUNCTION_NAME,
                                "memory_file": result["file_name"],
                            },
                        )
                        event = "memory_save" if result.get("ok") else "tool_result"
                        await job.publish(
                            event,
                            {
                                "name": memory.MEMORY_FUNCTION_NAME,
                                "tool": "sammy_memory",
                                "tool_display_name": "Memory",
                                "memory_file": result["file_name"],
                                "content": result["content"],
                            },
                        )
                        consecutive_tool_failures = 0 if result.get("ok") else consecutive_tool_failures + 1
                        if consecutive_tool_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                            raise RuntimeError("Sammy stopped after three consecutive tool failures.")
                        continue

                    if plugin_builder.is_builder_call(call["name"]):
                        action_label = "Proposing a tool" if call["name"] == plugin_builder.PROPOSE_FUNCTION_NAME else "Building tool"
                        await job.set_work_state(
                            "tool",
                            action_label,
                            f"Tool step {job.tool_step}",
                            tool_step=job.tool_step,
                        )
                        result = await asyncio.to_thread(
                            plugin_builder.handle_call,
                            job.conversation_id,
                            agent,
                            payload.message,
                            call["name"],
                            call["arguments"],
                            build_mode,
                        )
                        tool_message = db.add_message(
                            job.conversation_id,
                            "tool",
                            result["content"],
                            {
                                "tool_name": plugin_builder.BUILDER_TOOL_NAME,
                                "tool_display_name": "Tool Builder",
                                "function_name": call["name"],
                                "proposal_id": result.get("proposal_id"),
                                "proposal_status": result.get("status"),
                            },
                        )
                        await job.publish(
                            "tool_result",
                            {
                                "name": call["name"],
                                "tool": plugin_builder.BUILDER_TOOL_NAME,
                                "tool_display_name": "Tool Builder",
                                "content": result["content"],
                                "proposal_id": result.get("proposal_id"),
                                "proposal_status": result.get("status"),
                                "plugin_name": result.get("plugin_name"),
                                "message": tool_message,
                                "tool_step": job.tool_step,
                            },
                        )
                        consecutive_tool_failures = 0 if result.get("ok") else consecutive_tool_failures + 1
                        if consecutive_tool_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                            raise RuntimeError("Sammy stopped after three consecutive tool builder failures.")
                        continue

                    mapped_tool, mapped_name = function_map.get(call["name"], (None, call["name"]))
                    display_name = (mapped_tool.display_name or mapped_tool.name) if mapped_tool else mapped_name
                    await job.set_work_state(
                        "tool",
                        f"Using {display_name}",
                        f"Tool step {job.tool_step}",
                        tool_step=job.tool_step,
                    )
                    await job.publish(
                        "tool_start",
                        {
                            "name": mapped_name,
                            "tool": mapped_tool.name if mapped_tool else "",
                            "tool_display_name": display_name if mapped_tool else "",
                            "arguments": call["arguments"],
                            "tool_step": job.tool_step,
                        },
                    )

                    if mapped_tool:
                        refresh_notice = await asyncio.to_thread(check_tool_connection, mapped_tool.name)
                        if refresh_notice:
                            await job.publish("status", {"message": refresh_notice})

                    result = await asyncio.to_thread(reg.execute, call["name"], call["arguments"], enabled_tools)
                    requires_reconnect = mark_tool_needs_reconnect(result["tool_name"], result["content"])
                    tool_message = db.add_message(
                        job.conversation_id,
                        "tool",
                        result["content"],
                        {
                            "tool_name": result["tool_name"],
                            "tool_display_name": result.get("tool_display_name"),
                            "function_name": result["function_name"],
                        },
                    )
                    await job.publish(
                        "tool_result",
                        {
                            "name": result["function_name"],
                            "tool": result["tool_name"],
                            "tool_display_name": result.get("tool_display_name"),
                            "content": result["content"],
                            "requires_reconnect": requires_reconnect,
                            "message": tool_message,
                            "tool_step": job.tool_step,
                        },
                    )

                    if tool_result_failed(result["content"]):
                        consecutive_tool_failures += 1
                    else:
                        consecutive_tool_failures = 0
                    if consecutive_tool_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                        raise RuntimeError("Sammy stopped after three consecutive tool failures.")
                    if requires_reconnect:
                        await job.publish("status", {"message": f"{display_name} needs reconnecting."})
                    if job.stop_requested:
                        raise asyncio.CancelledError

                stalled_continuations = 0
                empty_attempts = 0
                continue

            if finish_reason in TOKEN_LIMIT_FINISH_REASONS:
                stalled_continuations = stalled_continuations + 1 if not segment_content else 0
                if stalled_continuations >= MAX_STALLED_CONTINUATIONS:
                    raise RuntimeError("The model repeatedly reached its limit without adding new response text.")
                continuation_part += 1
                continue

            if not answer and reasoning:
                empty_attempts += 1
                if empty_attempts < 2:
                    continuation_part += 1
                    pending_continue_prompt = EMPTY_RESPONSE_NUDGE
                    continue
            elif not answer:
                empty_attempts += 1
                if empty_attempts < 2:
                    continuation_part += 1
                    pending_continue_prompt = EMPTY_RESPONSE_NUDGE
                    continue

            if job.tool_step > 0 and answer and looks_like_lost_context_reply(answer) and lost_context_retries < 1:
                lost_context_retries += 1
                answer = ""
                reasoning = ""
                continuation_part = 1
                pending_continue_prompt = LOST_CONTEXT_NUDGE
                continue

            # Auto-continue: the model announced an action but didn't call a tool. Nudge it to
            # actually act (instead of the user having to type "continue"). Bounded + only when
            # real tools are available.
            if (
                plugin_tool_definitions
                and auto_continues < MAX_AUTO_CONTINUES
                and answer
                and finish_reason not in TOKEN_LIMIT_FINISH_REASONS
                and looks_like_unfinished_action(answer)
                and not awaiting_user_input(answer)
            ):
                auto_continues += 1
                db.add_message(
                    job.conversation_id,
                    "assistant",
                    answer,
                    {"tool_call_round": job.tool_step + 1, "reasoning": reasoning, "job_id": job.id, "auto_continue": True},
                )
                if auto_continues == 1:
                    system_prompt = f"{system_prompt}\n\n{AUTO_CONTINUE_NUDGE}"
                answer = ""
                reasoning = ""
                pending_continue_prompt = AUTO_CONTINUE_USER_PROMPT
                continuation_part = 1
                continue

            response_status = "" if answer else "empty"
            response_notice = "" if answer else "The model did not produce a final visible answer after retrying."
            metadata = background_job_metadata(
                job,
                reasoning,
                finish_reason,
                response_status=response_status,
                response_notice=response_notice,
                continuation_parts=continuation_part,
                context_compactions=context_compactions,
            )
            saved = db.add_message(job.conversation_id, "assistant", answer, metadata)
            await job.set_work_state("complete", "Complete", "Sammy finished the task.")
            await job.publish("assistant_message", saved)
            await job.publish("done", {"message": saved, "job": job.snapshot()})
            await job.set_terminal("complete", final_message=saved)
            memory.schedule_review(
                job.model,
                agent,
                job.conversation_id,
                user_message["content"] if user_message else payload.message,
                answer,
            )
            return

    except asyncio.CancelledError:
        metadata = background_job_metadata(
            job,
            reasoning,
            finish_reason,
            response_status="stopped",
            response_notice="Generation was stopped by the user before Sammy finished.",
            continuation_parts=continuation_part,
            context_compactions=context_compactions,
        )
        saved = db.add_message(job.conversation_id, "assistant", answer, metadata)
        await job.publish("assistant_message", saved)
        await job.publish("stopped", {"message": saved, "job": job.snapshot()})
        await job.set_terminal("stopped", final_message=saved)
    except Exception as exc:
        error_message = str(exc) or exc.__class__.__name__
        metadata = background_job_metadata(
            job,
            reasoning,
            finish_reason,
            response_status="error",
            response_notice="Sammy could not safely continue this task.",
            response_error=error_message,
            continuation_parts=continuation_part,
            context_compactions=context_compactions,
        )
        saved = db.add_message(job.conversation_id, "assistant", answer, metadata)
        await job.publish("error", {"message": error_message})
        await job.publish("assistant_message", saved)
        await job.publish("done", {"message": saved, "job": job.snapshot(), "status": "error"})
        await job.set_terminal("error", final_message=saved, error=error_message)


async def create_background_chat_job(payload: ChatPayload) -> Dict[str, Any]:
    active_jobs = chat_job_manager.active()
    if active_jobs:
        raise HTTPException(
            status_code=409,
            detail={"message": "Sammy is already working on a task.", "job": active_jobs[0].snapshot()},
        )

    settings = db.get_settings()
    if payload.conversation_id:
        try:
            conversation = db.get_conversation(payload.conversation_id)["conversation"]
        except KeyError:
            raise HTTPException(status_code=404, detail="Conversation not found")
        agent = db.get_agent(conversation.get("agent_id"))
    else:
        agent = db.get_agent(payload.agent_id)
        conversation = None

    model = payload.model or agent.get("model") or settings.get("default_model")
    model = resolve_model_name(model, await list_models())
    if not model:
        raise HTTPException(status_code=400, detail="No Ollama model available. Install one with `ollama pull`.")

    if not conversation:
        conversation = db.create_conversation("New chat", model, agent["id"])
        check_agent_tool_connections(agent)
    db.update_conversation(conversation["id"], {"model": model, "agent_id": agent["id"]})

    if payload.regenerate_from:
        db.replace_messages_after(conversation["id"], payload.regenerate_from)

    message_text = payload.message
    if payload.attachments:
        attachment_notes = []
        for upload_id in payload.attachments:
            upload = db.get_upload(upload_id)
            if upload:
                attachment_notes.append(f"Attached file: {upload['filename']} ({upload['path']})")
        if attachment_notes:
            message_text = f"{message_text}\n\n" + "\n".join(attachment_notes)

    user_message = None
    if not payload.regenerate_from:
        user_message = db.add_message(conversation["id"], "user", message_text)

    job = chat_job_manager.create(
        conversation["id"],
        agent["id"],
        model,
        user_message["id"] if user_message else payload.regenerate_from or "",
    )
    chat_job_manager.start(
        job,
        run_background_chat_job(job, payload, settings, agent, conversation, user_message),
    )
    return {"job": job.snapshot(), "conversation": conversation, "user_message": user_message}


@app.post("/api/chat/jobs")
async def create_chat_job(payload: ChatPayload) -> Dict[str, Any]:
    return await create_background_chat_job(payload)


@app.get("/api/chat/jobs/active")
async def active_chat_jobs() -> Dict[str, Any]:
    return {"jobs": [job.snapshot() for job in chat_job_manager.active()]}


@app.get("/api/chat/jobs/{job_id}")
async def get_chat_job(job_id: str) -> Dict[str, Any]:
    job = chat_job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chat job not found")
    return {"job": job.snapshot()}


@app.get("/api/chat/jobs/{job_id}/stream")
async def stream_chat_job(job_id: str, after: int = 0) -> StreamingResponse:
    job = chat_job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chat job not found")
    return StreamingResponse(
        job.event_stream(after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/jobs/{job_id}/stop")
async def stop_chat_job(job_id: str) -> Dict[str, Any]:
    job = chat_job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chat job not found")
    await chat_job_manager.stop(job)
    return {"job": job.snapshot()}


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    frontend = dist_dir()
    requested = frontend / path
    if path and requested.exists() and requested.is_file():
        return FileResponse(requested)
    index = frontend / "index.html"
    if index.exists():
        return FileResponse(index, headers=SPA_INDEX_HEADERS)
    return PlainTextResponse(
        "Sammy backend is running. Build the frontend with `cd frontend && npm run build`.",
        status_code=200,
    )
