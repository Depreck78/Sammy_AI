import json
import subprocess
from typing import Any, AsyncGenerator, Dict, List

import httpx

from .config import OLLAMA_URL


def human_bytes(value: int) -> str:
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


async def list_models() -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            data = response.json()
        models = []
        for model in data.get("models", []):
            details = model.get("details") or {}
            models.append(
                {
                    "name": model.get("name"),
                    "size": human_bytes(model.get("size") or 0),
                    "size_bytes": model.get("size") or 0,
                    "modified_at": model.get("modified_at"),
                    "parameter_size": details.get("parameter_size") or "",
                    "quantization_level": details.get("quantization_level") or "",
                }
            )
        return models
    except Exception:
        return list_models_from_cli()


def list_models_from_cli() -> List[Dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ollama", "list"],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return []
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    models = []
    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        size = " ".join(parts[2:4]) if len(parts) >= 4 else ""
        models.append(
            {
                "name": name,
                "size": size,
                "size_bytes": 0,
                "modified_at": "",
                "parameter_size": "",
                "quantization_level": "",
            }
        )
    return models


async def chat_stream(
    model: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    options: Dict[str, Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    think = options.pop("think", False)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "think": think,
    }
    if tools:
        payload["tools"] = tools

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    yield {"error": line}
