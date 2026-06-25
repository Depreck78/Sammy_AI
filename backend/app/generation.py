import json
from typing import Any, Dict, Iterable, List, Sequence, Tuple


CHARS_PER_TOKEN = 4
CONTEXT_MARGIN_TOKENS = 512
CONTINUATION_OVERLAP_CHARS = 1200
TOOL_ERROR_MARKERS = (
    " tool error:",
    "plugin function '",
    " is not enabled",
    "not connected",
    "unauthorized",
    "permission denied",
    "traceback",
)


def estimate_text_tokens(value: str) -> int:
    return max(1, (len(value or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: Sequence[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += 8
        total += estimate_text_tokens(str(message.get("role") or ""))
        total += estimate_text_tokens(str(message.get("content") or ""))
        if message.get("name"):
            total += estimate_text_tokens(str(message["name"]))
    return total


def estimate_tool_tokens(tools: Sequence[Dict[str, Any]]) -> int:
    if not tools:
        return 0
    return estimate_text_tokens(json.dumps(list(tools), ensure_ascii=False, sort_keys=True))


def context_input_budget(num_ctx: int, num_predict: int, tools: Sequence[Dict[str, Any]]) -> int:
    output_reserve = min(max(256, num_predict), max(512, num_ctx // 2))
    return max(768, num_ctx - output_reserve - estimate_tool_tokens(tools) - CONTEXT_MARGIN_TOKENS)


def split_history_for_compaction(
    messages: Sequence[Dict[str, Any]],
    recent_budget: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    system_messages = [dict(message) for message in messages if message.get("role") == "system"]
    conversation = [dict(message) for message in messages if message.get("role") != "system"]
    recent: List[Dict[str, Any]] = []
    used = 0

    for message in reversed(conversation):
        cost = estimate_messages_tokens([message])
        if recent and used + cost > recent_budget:
            break
        recent.append(message)
        used += cost

    recent.reverse()
    older_count = len(conversation) - len(recent)
    return system_messages, conversation[:older_count], recent


def render_compaction_source(messages: Iterable[Dict[str, Any]], max_chars: int) -> str:
    rendered: List[str] = []
    for message in messages:
        role = str(message.get("role") or "message").upper()
        name = str(message.get("name") or "")
        content = str(message.get("content") or "")
        rendered.append(f"[{role}{f' {name}' if name else ''}]\n{content}")
    source = "\n\n".join(rendered)
    if len(source) <= max_chars:
        return source
    marker = "\n\n[...older compacted content omitted...]\n\n"
    head_chars = max(400, max_chars // 3)
    tail_chars = max(400, max_chars - head_chars - len(marker))
    if head_chars + tail_chars + len(marker) >= len(source):
        return source
    return f"{source[:head_chars]}{marker}{source[-tail_chars:]}"


def strip_continuation_overlap(existing: str, continuation: str) -> str:
    if not existing or not continuation:
        return continuation
    maximum = min(len(existing), len(continuation), CONTINUATION_OVERLAP_CHARS)
    for size in range(maximum, 19, -1):
        if existing[-size:] == continuation[:size]:
            return continuation[size:]
    return continuation


def tool_call_signature(name: str, arguments: Dict[str, Any]) -> str:
    return f"{name}:{json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, default=str)}"


def tool_result_failed(content: str) -> bool:
    lowered = f" {content or ''}".lower()
    return any(marker in lowered for marker in TOOL_ERROR_MARKERS)
