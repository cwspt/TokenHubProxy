from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


LOG = logging.getLogger("tokenhub_proxy")
logging.basicConfig(
    level=os.getenv("PROXY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)


@dataclass(frozen=True)
class Settings:
    tokenhub_api_key: str
    tokenhub_base_url: str
    tokenhub_model: str
    proxy_key: str
    request_timeout_seconds: float
    enable_tool_calls: bool
    response_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            tokenhub_api_key=os.getenv("TOKENHUB_API_KEY", ""),
            tokenhub_base_url=os.getenv(
                "TOKENHUB_BASE_URL",
                "https://tokenhub.tencentmaas.com/plan/v3/chat/completions",
            ),
            tokenhub_model=os.getenv("TOKENHUB_MODEL", "glm-5.1"),
            proxy_key=os.getenv("CODEX_GLM_PROXY_KEY", ""),
            request_timeout_seconds=float(os.getenv("PROXY_REQUEST_TIMEOUT_SECONDS", "600")),
            enable_tool_calls=os.getenv("ENABLE_TOOL_CALLS", "false").lower()
            in {"1", "true", "yes", "on"},
            response_ttl_seconds=int(os.getenv("RESPONSE_STORE_TTL_SECONDS", "3600")),
        )


@dataclass
class StoredResponse:
    expires_at: float
    messages: list[dict[str, Any]]
    output: list[dict[str, Any]]


@dataclass
class ProxyMetrics:
    started_at: int
    requests_started: int = 0
    requests_completed: int = 0
    requests_failed: int = 0
    request_text_chars: int = 0
    response_text_chars: int = 0
    response_tool_call_chars: int = 0
    upstream_prompt_tokens: int = 0
    upstream_completion_tokens: int = 0
    upstream_total_tokens: int = 0


SETTINGS = Settings.from_env()
RESPONSES: dict[str, StoredResponse] = {}
METRICS = ProxyMetrics(started_at=int(time.time()))
app = FastAPI(title="TokenHub Responses Proxy", version="0.1.0")
IGNORED_RESPONSES_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
    "computer_use_preview",
    "file_search",
    "code_interpreter",
    "image_generation",
    "local_shell",
    "mcp",
}


def now_unix() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def prune_response_store() -> None:
    now = time.time()
    expired = [key for key, value in RESPONSES.items() if value.expires_at <= now]
    for key in expired:
        RESPONSES.pop(key, None)


def require_proxy_auth(authorization: str | None) -> None:
    if not SETTINGS.proxy_key:
        raise HTTPException(
            status_code=500,
            detail="CODEX_GLM_PROXY_KEY is not configured for local proxy auth",
        )
    expected = f"Bearer {SETTINGS.proxy_key}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid local proxy bearer token")


def require_upstream_key() -> None:
    if not SETTINGS.tokenhub_api_key:
        raise HTTPException(status_code=500, detail="TOKENHUB_API_KEY is not configured")


def text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in {"input_text", "output_text", "text"}:
                parts.append(str(part.get("text", "")))
            elif "text" in part and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif part_type in {"input_image", "image_url"}:
                image_url = part.get("image_url") or part.get("url")
                if image_url:
                    parts.append(f"[image: {image_url}]")
            elif part_type in {"refusal"}:
                parts.append(str(part.get("refusal", "")))
        return "\n".join(p for p in parts if p)
    return str(content)


def message_text_char_count(messages: list[dict[str, Any]]) -> int:
    return sum(len(text_from_content(message.get("content"))) for message in messages)


def output_tool_call_char_count(output: list[dict[str, Any]]) -> int:
    total = 0
    for item in output:
        item_type = item.get("type")
        if item_type == "function_call":
            total += len(str(item.get("arguments") or ""))
        elif item_type == "custom_tool_call":
            total += len(text_from_content(item.get("input", "")))
    return total


def usage_int(usage: Any, key: str) -> int:
    if not isinstance(usage, dict):
        return 0
    value = usage.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def record_request_metrics(request_messages: list[dict[str, Any]]) -> None:
    METRICS.requests_started += 1
    METRICS.request_text_chars += message_text_char_count(request_messages)


def record_completed_metrics(output_text: str, output: list[dict[str, Any]], usage: Any) -> None:
    METRICS.requests_completed += 1
    METRICS.response_text_chars += len(output_text)
    METRICS.response_tool_call_chars += output_tool_call_char_count(output)
    METRICS.upstream_prompt_tokens += usage_int(usage, "prompt_tokens")
    METRICS.upstream_completion_tokens += usage_int(usage, "completion_tokens")
    METRICS.upstream_total_tokens += usage_int(usage, "total_tokens")


def record_failed_metrics() -> None:
    METRICS.requests_failed += 1


def metrics_snapshot() -> dict[str, Any]:
    total_chars = (
        METRICS.request_text_chars
        + METRICS.response_text_chars
        + METRICS.response_tool_call_chars
    )
    return {
        "started_at": METRICS.started_at,
        "requests": {
            "started": METRICS.requests_started,
            "completed": METRICS.requests_completed,
            "failed": METRICS.requests_failed,
        },
        "chars": {
            "request_text": METRICS.request_text_chars,
            "response_text": METRICS.response_text_chars,
            "response_tool_calls": METRICS.response_tool_call_chars,
            "total_counted": total_chars,
        },
        "upstream_usage_tokens": {
            "prompt": METRICS.upstream_prompt_tokens,
            "completion": METRICS.upstream_completion_tokens,
            "total": METRICS.upstream_total_tokens,
        },
        "notes": [
            "Counters are process-local and reset when the proxy restarts.",
            "Only character counts and upstream usage numbers are retained; dialogue text is not stored.",
            "Character counts are not tokenizer-exact token counts.",
        ],
    }


def normalize_tool_call(raw: dict[str, Any]) -> dict[str, Any]:
    function = raw.get("function") or {}
    name = function.get("name") or raw.get("name")
    arguments = function.get("arguments") or raw.get("arguments") or "{}"
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return {
        "id": raw.get("id") or raw.get("call_id") or new_id("call"),
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def response_function_call_to_chat_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    arguments = item.get("arguments", "{}")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return {
        "id": item.get("call_id") or item.get("id") or new_id("call"),
        "type": "function",
        "function": {
            "name": item.get("name"),
            "arguments": arguments,
        },
    }


def response_custom_tool_call_to_chat_tool_call(item: dict[str, Any]) -> dict[str, Any]:
    arguments = {"input": text_from_content(item.get("input", ""))}
    return {
        "id": item.get("call_id") or item.get("id") or new_id("call"),
        "type": "function",
        "function": {
            "name": item.get("name"),
            "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
        },
    }


def custom_tool_input_from_arguments(arguments: Any) -> str:
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    if isinstance(parsed, dict) and "input" in parsed:
        return text_from_content(parsed.get("input"))
    return text_from_content(parsed)


def input_item_to_messages(item: dict[str, Any]) -> list[dict[str, Any]]:
    item_type = item.get("type")
    if item_type == "message" or ("role" in item and "content" in item):
        role = item.get("role", "user")
        if role == "developer":
            role = "system"
        if role not in {"system", "user", "assistant", "tool"}:
            raise HTTPException(status_code=400, detail=f"Unsupported message role: {role}")
        message: dict[str, Any] = {"role": role, "content": text_from_content(item.get("content"))}
        if item.get("tool_call_id"):
            message["tool_call_id"] = item["tool_call_id"]
        if item.get("tool_calls"):
            message["tool_calls"] = [normalize_tool_call(call) for call in item["tool_calls"]]
        return [message]

    if item_type == "function_call_output":
        call_id = item.get("call_id") or item.get("tool_call_id")
        if not call_id:
            raise HTTPException(status_code=400, detail="function_call_output missing call_id")
        return [
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": text_from_content(item.get("output", "")),
            }
        ]

    if item_type == "custom_tool_call_output":
        call_id = item.get("call_id") or item.get("tool_call_id")
        if not call_id:
            raise HTTPException(status_code=400, detail="custom_tool_call_output missing call_id")
        return [
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": text_from_content(item.get("output", "")),
            }
        ]

    if item_type == "function_call":
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [response_function_call_to_chat_tool_call(item)],
            }
        ]

    if item_type == "custom_tool_call":
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [response_custom_tool_call_to_chat_tool_call(item)],
            }
        ]

    if item_type in {"reasoning", "summary"}:
        return []

    raise HTTPException(status_code=400, detail=f"Unsupported Responses input item type: {item_type}")


def responses_input_to_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": str(instructions)})

    previous_response_id = payload.get("previous_response_id")
    if previous_response_id:
        prune_response_store()
        stored = RESPONSES.get(previous_response_id)
        if stored:
            messages.extend(stored.messages)
        else:
            LOG.info("previous_response_id_not_found previous_response_id=%s", previous_response_id)

    input_value = payload.get("input")
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
    elif isinstance(input_value, list):
        for item in input_value:
            if isinstance(item, str):
                messages.append({"role": "user", "content": item})
            elif isinstance(item, dict):
                messages.extend(input_item_to_messages(item))
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported Responses input list value: {type(item).__name__}",
                )
    elif input_value is None:
        pass
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported Responses input type: {type(input_value).__name__}")

    return messages


def responses_tools_to_chat_tools(tools: Any) -> tuple[list[dict[str, Any]] | None, set[str]]:
    if not tools:
        return None, set()
    if not isinstance(tools, list):
        raise HTTPException(status_code=400, detail="Responses tools must be a list")

    chat_tools: list[dict[str, Any]] = []
    custom_tool_names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            raise HTTPException(status_code=400, detail="Responses tool entries must be objects")
        tool_type = tool.get("type")
        if tool_type == "function":
            function_def = {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
            }
            if "strict" in tool:
                function_def["strict"] = tool["strict"]
            chat_tools.append({"type": "function", "function": function_def})
            continue

        if tool_type == "custom":
            name = tool.get("name")
            if not name:
                raise HTTPException(status_code=400, detail="Responses custom tool missing name")
            custom_tool_names.add(str(name))
            description = tool.get("description", "")
            format_value = tool.get("format")
            if format_value:
                description = f"{description}\nInput format: {json.dumps(format_value, ensure_ascii=False, separators=(',', ':'))}".strip()
            chat_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "input": {
                                    "type": "string",
                                    "description": "Free-form input for this custom tool.",
                                }
                            },
                            "required": ["input"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
            continue

        if tool_type in IGNORED_RESPONSES_TOOL_TYPES:
            LOG.info("ignored_responses_tool_type tool_type=%s", tool_type)
            continue

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported Responses tool type: {tool.get('type')}")

    return (chat_tools or None), custom_tool_names


def responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    if tool_choice in (None, "auto", "none", "required"):
        return tool_choice
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") in {"function", "custom"}:
            name = tool_choice.get("name")
            if not name and isinstance(tool_choice.get("function"), dict):
                name = tool_choice["function"].get("name")
            return {"type": "function", "function": {"name": name}}
        if tool_choice.get("type") in IGNORED_RESPONSES_TOOL_TYPES:
            return "auto"
    return tool_choice


def build_chat_payload(payload: dict[str, Any], stream: bool) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    messages = responses_input_to_messages(payload)
    chat_payload: dict[str, Any] = {
        "model": os.getenv("TOKENHUB_MODEL", payload.get("model") or SETTINGS.tokenhub_model),
        "messages": messages,
        "stream": stream,
    }

    tools, custom_tool_names = responses_tools_to_chat_tools(payload.get("tools"))
    if tools:
        if not SETTINGS.enable_tool_calls:
            raise HTTPException(status_code=400, detail="TokenHub/GLM tool_calls unsupported by probe")
        chat_payload["tools"] = tools
        if "tool_choice" in payload:
            chat_payload["tool_choice"] = responses_tool_choice_to_chat(payload.get("tool_choice"))

    passthrough = {
        "temperature": "temperature",
        "top_p": "top_p",
        "presence_penalty": "presence_penalty",
        "frequency_penalty": "frequency_penalty",
        "stop": "stop",
        "seed": "seed",
        "max_output_tokens": "max_tokens",
        "max_tokens": "max_tokens",
    }
    for source, target in passthrough.items():
        if source in payload and payload[source] is not None:
            chat_payload[target] = payload[source]

    return chat_payload, messages, custom_tool_names


def chat_message_to_response_output(
    message: dict[str, Any],
    custom_tool_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    custom_tool_names = custom_tool_names or set()
    output: list[dict[str, Any]] = []
    text = message.get("content") or ""
    if text:
        output.append(
            {
                "id": new_id("msg"),
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        )

    for raw_tool_call in message.get("tool_calls") or []:
        tool_call = normalize_tool_call(raw_tool_call)
        name = tool_call["function"].get("name")
        if name in custom_tool_names:
            output.append(
                {
                    "id": new_id("ctc"),
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": tool_call["id"],
                    "name": name,
                    "input": custom_tool_input_from_arguments(tool_call["function"].get("arguments") or "{}"),
                }
            )
        else:
            output.append(
                {
                    "id": new_id("fc"),
                    "type": "function_call",
                    "status": "completed",
                    "call_id": tool_call["id"],
                    "name": name,
                    "arguments": tool_call["function"].get("arguments") or "{}",
                }
            )

    return output, text


def response_output_to_chat_messages(output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []
    pending_text: list[str] = []

    for item in output:
        item_type = item.get("type")
        if item_type == "message":
            text = text_from_content(item.get("content"))
            if text:
                pending_text.append(text)
        elif item_type == "function_call":
            pending_tool_calls.append(response_function_call_to_chat_tool_call(item))
        elif item_type == "custom_tool_call":
            pending_tool_calls.append(response_custom_tool_call_to_chat_tool_call(item))

    if pending_text or pending_tool_calls:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(pending_text) if pending_text else None,
        }
        if pending_tool_calls:
            message["tool_calls"] = pending_tool_calls
        messages.append(message)
    return messages


def build_response_json(
    response_id: str,
    model: str,
    output: list[dict[str, Any]],
    output_text: str,
    usage: Any,
    status: str = "completed",
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": now_unix(),
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": model,
        "output": output,
        "output_text": output_text,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": usage,
        "user": None,
        "metadata": {},
    }


def store_response(response_id: str, request_messages: list[dict[str, Any]], output: list[dict[str, Any]]) -> None:
    prune_response_store()
    messages = [*request_messages, *response_output_to_chat_messages(output)]
    RESPONSES[response_id] = StoredResponse(
        expires_at=time.time() + SETTINGS.response_ttl_seconds,
        messages=messages,
        output=output,
    )


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def map_httpx_error(status_code: int) -> int:
    if status_code in {401, 403, 429}:
        return status_code
    if status_code >= 500:
        return 503
    if status_code >= 400:
        return 502
    return status_code


def upstream_error_detail(status_code: int) -> str:
    if status_code in {401, 403}:
        return "TokenHub authentication failed; check TOKENHUB_API_KEY"
    if status_code == 429:
        return "TokenHub rate limit exceeded"
    if status_code >= 500:
        return "TokenHub upstream server error"
    return "TokenHub upstream request failed"


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": SETTINGS.tokenhub_model,
        "tool_calls_enabled": SETTINGS.enable_tool_calls,
        "metrics": metrics_snapshot(),
    }


@app.get("/metrics")
async def metrics(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_proxy_auth(authorization)
    return metrics_snapshot()


@app.get("/v1/models")
async def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_proxy_auth(authorization)
    return {
        "object": "list",
        "data": [
            {
                "id": SETTINGS.tokenhub_model,
                "object": "model",
                "created": 0,
                "owned_by": "tencent-tokenhub",
            }
        ],
    }


@app.post("/v1/responses", response_model=None)
async def create_response(request: Request, authorization: str | None = Header(default=None)):
    require_proxy_auth(authorization)
    require_upstream_key()
    request_id = new_id("req")
    started = time.perf_counter()
    payload = await request.json()
    stream = bool(payload.get("stream", False))
    chat_payload, request_messages, custom_tool_names = build_chat_payload(payload, stream=stream)
    record_request_metrics(request_messages)
    model = chat_payload["model"]
    LOG.info("request_start request_id=%s model=%s stream=%s", request_id, model, stream)

    if stream:
        return StreamingResponse(
            stream_response(request_id, chat_payload, request_messages, custom_tool_names, started),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async with httpx.AsyncClient(timeout=SETTINGS.request_timeout_seconds) as client:
        try:
            upstream = await client.post(
                SETTINGS.tokenhub_base_url,
                headers={
                    "Authorization": f"Bearer {SETTINGS.tokenhub_api_key}",
                    "Content-Type": "application/json",
                },
                json=chat_payload,
            )
        except httpx.TimeoutException:
            record_failed_metrics()
            LOG.info("request_done request_id=%s model=%s status=timeout elapsed_ms=%d", request_id, model, int((time.perf_counter() - started) * 1000))
            raise HTTPException(status_code=504, detail="TokenHub upstream request timed out") from None
        except httpx.HTTPError:
            record_failed_metrics()
            LOG.info("request_done request_id=%s model=%s status=http_error elapsed_ms=%d", request_id, model, int((time.perf_counter() - started) * 1000))
            raise HTTPException(status_code=502, detail="TokenHub upstream connection failed") from None

    if upstream.status_code >= 400:
        record_failed_metrics()
        status_code = map_httpx_error(upstream.status_code)
        LOG.info(
            "request_done request_id=%s model=%s status=upstream_error upstream_status=%s elapsed_ms=%d",
            request_id,
            model,
            upstream.status_code,
            int((time.perf_counter() - started) * 1000),
        )
        raise HTTPException(status_code=status_code, detail=upstream_error_detail(upstream.status_code))

    data = upstream.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output, output_text = chat_message_to_response_output(message, custom_tool_names)
    response_id = new_id("resp")
    response_json = build_response_json(
        response_id=response_id,
        model=model,
        output=output,
        output_text=output_text,
        usage=data.get("usage"),
    )
    store_response(response_id, request_messages, output)
    record_completed_metrics(output_text, output, data.get("usage"))
    LOG.info(
        "request_done request_id=%s response_id=%s model=%s status=completed elapsed_ms=%d",
        request_id,
        response_id,
        model,
        int((time.perf_counter() - started) * 1000),
    )
    return JSONResponse(response_json)


async def stream_response(
    request_id: str,
    chat_payload: dict[str, Any],
    request_messages: list[dict[str, Any]],
    custom_tool_names: set[str],
    started: float,
) -> AsyncIterator[str]:
    model = chat_payload["model"]
    response_id = new_id("resp")
    created_response = build_response_json(response_id, model, [], "", None, status="in_progress")
    yield sse("response.created", {"type": "response.created", "response": created_response})
    yield sse("response.in_progress", {"type": "response.in_progress", "response": created_response})

    output: list[dict[str, Any]] = []
    text_item_id: str | None = None
    text_output = ""
    tool_items: dict[str, dict[str, Any]] = {}
    tool_call_order: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=SETTINGS.request_timeout_seconds) as client:
            async with client.stream(
                "POST",
                SETTINGS.tokenhub_base_url,
                headers={
                    "Authorization": f"Bearer {SETTINGS.tokenhub_api_key}",
                    "Content-Type": "application/json",
                },
                json=chat_payload,
            ) as upstream:
                if upstream.status_code >= 400:
                    record_failed_metrics()
                    yield sse(
                        "error",
                        {
                            "type": "error",
                            "code": str(upstream.status_code),
                            "message": upstream_error_detail(upstream.status_code),
                        },
                    )
                    LOG.info(
                        "request_done request_id=%s model=%s status=upstream_error upstream_status=%s elapsed_ms=%d",
                        request_id,
                        model,
                        upstream.status_code,
                        int((time.perf_counter() - started) * 1000),
                    )
                    return

                async for line in upstream.aiter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line.removeprefix("data:").strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}

                    content_delta = delta.get("content") or ""
                    if content_delta:
                        if text_item_id is None:
                            text_item_id = new_id("msg")
                            item = {
                                "id": text_item_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            }
                            yield sse(
                                "response.output_item.added",
                                {"type": "response.output_item.added", "output_index": 0, "item": item},
                            )
                            yield sse(
                                "response.content_part.added",
                                {
                                    "type": "response.content_part.added",
                                    "item_id": text_item_id,
                                    "output_index": 0,
                                    "content_index": 0,
                                    "part": {"type": "output_text", "text": "", "annotations": []},
                                },
                            )
                        text_output += content_delta
                        yield sse(
                            "response.output_text.delta",
                            {
                                "type": "response.output_text.delta",
                                "item_id": text_item_id,
                                "output_index": 0,
                                "content_index": 0,
                                "delta": content_delta,
                            },
                        )

                    for raw_tool_call in delta.get("tool_calls") or []:
                        index = str(raw_tool_call.get("index", len(tool_call_order)))
                        existing = tool_items.get(index)
                        if existing is None:
                            call_id = raw_tool_call.get("id") or new_id("call")
                            function = raw_tool_call.get("function") or {}
                            name = function.get("name") or ""
                            is_custom = name in custom_tool_names
                            existing = {
                                "id": new_id("ctc" if is_custom else "fc"),
                                "type": "custom_tool_call" if is_custom else "function_call",
                                "status": "in_progress",
                                "call_id": call_id,
                                "name": name,
                                "arguments": "",
                            }
                            if is_custom:
                                existing["input"] = ""
                            tool_items[index] = existing
                            tool_call_order.append(index)
                            output_index = (1 if text_item_id is not None else 0) + len(tool_call_order) - 1
                            yield sse(
                                "response.output_item.added",
                                {
                                    "type": "response.output_item.added",
                                    "output_index": output_index,
                                    "item": existing,
                                },
                            )
                        function = raw_tool_call.get("function") or {}
                        if function.get("name") and not existing.get("name"):
                            existing["name"] = function["name"]
                            if existing["name"] in custom_tool_names and existing.get("type") != "custom_tool_call":
                                existing["id"] = new_id("ctc")
                                existing["type"] = "custom_tool_call"
                                existing["input"] = ""
                        arg_delta = function.get("arguments") or ""
                        if arg_delta:
                            existing["arguments"] += arg_delta
                            if existing.get("type") != "custom_tool_call":
                                yield sse(
                                    "response.function_call_arguments.delta",
                                    {
                                        "type": "response.function_call_arguments.delta",
                                        "item_id": existing["id"],
                                        "output_index": (1 if text_item_id is not None else 0) + tool_call_order.index(index),
                                        "delta": arg_delta,
                                    },
                                )
    except httpx.TimeoutException:
        record_failed_metrics()
        yield sse("error", {"type": "error", "code": "timeout", "message": "TokenHub upstream request timed out"})
        LOG.info("request_done request_id=%s model=%s status=timeout elapsed_ms=%d", request_id, model, int((time.perf_counter() - started) * 1000))
        return
    except httpx.HTTPError:
        record_failed_metrics()
        yield sse("error", {"type": "error", "code": "upstream_connection_failed", "message": "TokenHub upstream connection failed"})
        LOG.info("request_done request_id=%s model=%s status=http_error elapsed_ms=%d", request_id, model, int((time.perf_counter() - started) * 1000))
        return
    except asyncio.CancelledError:
        LOG.info("request_done request_id=%s model=%s status=client_cancelled elapsed_ms=%d", request_id, model, int((time.perf_counter() - started) * 1000))
        raise

    if text_item_id is not None:
        text_item = {
            "id": text_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text_output, "annotations": []}],
        }
        output.append(text_item)
        yield sse(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "item_id": text_item_id,
                "output_index": 0,
                "content_index": 0,
                "text": text_output,
            },
        )
        yield sse(
            "response.content_part.done",
            {
                "type": "response.content_part.done",
                "item_id": text_item_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": text_output, "annotations": []},
            },
        )
        yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": text_item})

    for index in tool_call_order:
        item = tool_items[index]
        item["status"] = "completed"
        if item.get("type") == "custom_tool_call":
            item["input"] = custom_tool_input_from_arguments(item.pop("arguments", ""))
        else:
            item["arguments"] = item.get("arguments", "")
        output.append(item)
        output_index = len(output) - 1
        if item.get("type") == "custom_tool_call":
            yield sse(
                "response.custom_tool_call_input.done",
                {
                    "type": "response.custom_tool_call_input.done",
                    "item_id": item["id"],
                    "output_index": output_index,
                    "input": item.get("input", ""),
                },
            )
        else:
            yield sse(
                "response.function_call_arguments.done",
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": item["id"],
                    "output_index": output_index,
                    "arguments": item.get("arguments", ""),
                },
            )
        yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": output_index, "item": item})

    completed = build_response_json(response_id, model, output, text_output, None, status="completed")
    store_response(response_id, request_messages, output)
    record_completed_metrics(text_output, output, None)
    yield sse("response.completed", {"type": "response.completed", "response": completed})
    LOG.info(
        "request_done request_id=%s response_id=%s model=%s status=completed elapsed_ms=%d",
        request_id,
        response_id,
        model,
        int((time.perf_counter() - started) * 1000),
    )
