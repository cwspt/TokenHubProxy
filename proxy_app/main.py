from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
    upstream_tool_choice_mode: str
    response_language_instruction: str
    response_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        tool_choice_mode = os.getenv("UPSTREAM_TOOL_CHOICE_MODE", "passthrough").strip().lower()
        if tool_choice_mode not in {"passthrough", "omit_forced"}:
            tool_choice_mode = "passthrough"
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
            upstream_tool_choice_mode=tool_choice_mode,
            response_language_instruction=os.getenv("RESPONSE_LANGUAGE_INSTRUCTION", "").strip(),
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
TOOL_CALL_REASONING: dict[str, tuple[float, str]] = {}
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
    "namespace",
}
MAX_UPSTREAM_ERROR_DETAIL_CHARS = 500


def now_unix() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def prune_response_store() -> None:
    now = time.time()
    expired = [key for key, value in RESPONSES.items() if value.expires_at <= now]
    for key in expired:
        RESPONSES.pop(key, None)
    expired_tool_calls = [key for key, value in TOOL_CALL_REASONING.items() if value[0] <= now]
    for key in expired_tool_calls:
        TOOL_CALL_REASONING.pop(key, None)


def remember_tool_call_reasoning(output: list[dict[str, Any]], reasoning_content: str) -> None:
    if not reasoning_content:
        return
    expires_at = time.time() + SETTINGS.response_ttl_seconds
    for item in output:
        if item.get("type") not in {"function_call", "custom_tool_call"}:
            continue
        call_id = item.get("call_id")
        if call_id:
            TOOL_CALL_REASONING[str(call_id)] = (expires_at, reasoning_content)


def reasoning_content_for_call_id(call_id: Any) -> str:
    if not call_id:
        return ""
    prune_response_store()
    stored = TOOL_CALL_REASONING.get(str(call_id))
    if not stored:
        return ""
    return stored[1]


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


def chat_tool_names(chat_tools: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(chat_tools, list):
        return names
    for tool in chat_tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            names.add(str(function["name"]))
    return names


def tool_name_aliases_for_chat_tools(chat_tools: Any) -> dict[str, str]:
    names = chat_tool_names(chat_tools)
    aliases: dict[str, str] = {}
    if "shell_command" in names and "shell" not in names:
        aliases["shell"] = "shell_command"
    return aliases


def resolve_tool_call_name(name: Any, aliases: dict[str, str]) -> Any:
    if isinstance(name, str) and name in aliases:
        replacement = aliases[name]
        LOG.info("aliased_tool_call_name from=%s to=%s", name, replacement)
        return replacement
    return name


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
        if role == "assistant" and item.get("reasoning_content"):
            message["reasoning_content"] = text_from_content(item.get("reasoning_content"))
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
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [response_function_call_to_chat_tool_call(item)],
        }
        reasoning_content = reasoning_content_for_call_id(item.get("call_id") or item.get("id"))
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        return [message]

    if item_type == "custom_tool_call":
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [response_custom_tool_call_to_chat_tool_call(item)],
        }
        reasoning_content = reasoning_content_for_call_id(item.get("call_id") or item.get("id"))
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        return [message]

    if item_type == "reasoning":
        reasoning_text = ""
        for summary in item.get("summary") or []:
            if summary.get("text"):
                if not reasoning_text:
                    reasoning_text = summary["text"]
                else:
                    reasoning_text += "\n" + summary["text"]
        if reasoning_text:
            return [{"role": "assistant", "content": None, "_reasoning_content": reasoning_text}]
        return []

    if item_type == "summary":
        return []

    raise HTTPException(status_code=400, detail=f"Unsupported Responses input item type: {item_type}")


def responses_input_to_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if SETTINGS.response_language_instruction:
        messages.append({"role": "system", "content": SETTINGS.response_language_instruction})

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

    # Merge _reasoning_content markers from "reasoning" items into the
    # nearest assistant message so DeepSeek thinking mode round-trips work.
    # Reasoning items may appear before or after the assistant message.
    pending_reasoning: list[str] = []
    merged: list[dict[str, Any]] = []
    for message in messages:
        if message.get("_reasoning_content"):
            rc = message.pop("_reasoning_content")
            # Try to attach to a preceding assistant message
            attached = False
            for i in range(len(merged) - 1, -1, -1):
                if merged[i].get("role") == "assistant":
                    merged[i]["reasoning_content"] = (
                        (merged[i].get("reasoning_content") or "") + "\n" + rc
                    ).lstrip("\n")
                    attached = True
                    break
            if not attached:
                pending_reasoning.append(rc)
            continue
        if message.get("role") == "assistant" and pending_reasoning:
            message["reasoning_content"] = "\n".join(pending_reasoning) + (
                ("\n" + message["reasoning_content"]) if message.get("reasoning_content") else ""
            )
            pending_reasoning.clear()
        merged.append(message)
    messages = merged

    # Merge consecutive assistant messages that only carry tool_calls
    # (no text content) into a single assistant message.  This avoids
    # interleaved assistant/tool ordering that causes
    # normalize_chat_messages_for_upstream to drop complete blocks.
    if len(messages) > 1:
        compacted: list[dict[str, Any]] = []
        for message in messages:
            if (
                message.get("role") == "assistant"
                and not message.get("content")
                and message.get("tool_calls")
                and not message.get("reasoning_content")
                and compacted
                and compacted[-1].get("role") == "assistant"
                and not compacted[-1].get("content")
                and not compacted[-1].get("reasoning_content")
            ):
                compacted[-1].setdefault("tool_calls", []).extend(message.get("tool_calls") or [])
            else:
                compacted.append(message)
        messages = compacted

    return messages


def responses_tools_to_chat_tools(tools: Any) -> tuple[list[dict[str, Any]] | None, set[str]]:
    if not tools:
        return None, set()
    if not isinstance(tools, list):
        raise HTTPException(status_code=400, detail="Responses tools must be a list")

    chat_tools: list[dict[str, Any]] = []
    custom_tool_names: set[str] = set()
    tool_summaries: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise HTTPException(status_code=400, detail="Responses tool entries must be objects")
        tool_type = tool.get("type")
        tool_name = tool.get("name")
        if tool_type == "function" and not tool_name and isinstance(tool.get("function"), dict):
            tool_name = tool["function"].get("name")
        tool_summaries.append(f"{tool_type}:{tool_name or '-'}")
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
            LOG.info("ignored_responses_tool_type tool_type=%s tool_name=%s", tool_type, tool_name or "-")
            continue

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported Responses tool type: {tool.get('type')}")

    if tool_summaries:
        LOG.info("responses_tools_summary tools=%s", ",".join(tool_summaries))
    return (chat_tools or None), custom_tool_names


def responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    if SETTINGS.upstream_tool_choice_mode == "omit_forced":
        if tool_choice in (None, "auto", "none"):
            return tool_choice
        if isinstance(tool_choice, dict) and tool_choice.get("type") in IGNORED_RESPONSES_TOOL_TYPES:
            return "auto"
        return None
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


def message_tool_call_ids(message: dict[str, Any]) -> set[str]:
    call_ids: set[str] = set()
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        call_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
        if call_id:
            call_ids.add(str(call_id))
    return call_ids


def message_has_tool_calls(message: dict[str, Any]) -> bool:
    return bool(message.get("tool_calls"))


def normalize_chat_messages_for_upstream(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    dropped_orphan_tool_messages = 0
    dropped_incomplete_tool_blocks = 0
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "tool":
            dropped_orphan_tool_messages += 1
            index += 1
            continue

        expected_tool_call_ids = message_tool_call_ids(message)
        if message.get("role") != "assistant" or not message_has_tool_calls(message):
            normalized.append(message)
            index += 1
            continue

        tool_messages: list[dict[str, Any]] = []
        seen_tool_call_ids: set[str] = set()
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            tool_message = messages[cursor]
            tool_call_id = tool_message.get("tool_call_id")
            if tool_call_id:
                seen_tool_call_ids.add(str(tool_call_id))
            tool_messages.append(tool_message)
            cursor += 1

        if expected_tool_call_ids and expected_tool_call_ids.issubset(seen_tool_call_ids):
            normalized.append(message)
            normalized.extend(tool_messages)
        else:
            dropped_incomplete_tool_blocks += 1
            dropped_orphan_tool_messages += len(tool_messages)
        index = cursor

    if dropped_orphan_tool_messages or dropped_incomplete_tool_blocks:
        LOG.info(
            "normalized_chat_messages dropped_incomplete_tool_blocks=%s dropped_orphan_tool_messages=%s",
            dropped_incomplete_tool_blocks,
            dropped_orphan_tool_messages,
        )

    # Final pass: prune individual tool_calls from kept blocks whose
    # tool messages were lost in a different dropped block.
    pruned_tool_calls = 0
    all_tool_message_ids: set[str] = set()
    for message in normalized:
        if message.get("role") == "tool" and message.get("tool_call_id"):
            all_tool_message_ids.add(str(message["tool_call_id"]))
    cleaned: list[dict[str, Any]] = []
    for message in normalized:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            original_calls = message["tool_calls"]
            kept_calls = [
                call for call in original_calls
                if not isinstance(call, dict)
                or (call.get("id") or call.get("call_id") or call.get("tool_call_id"))
                    and str(call.get("id") or call.get("call_id") or call.get("tool_call_id")) in all_tool_message_ids
            ]
            pruned_tool_calls += len(original_calls) - len(kept_calls)
            if kept_calls:
                message = dict(message)
                message["tool_calls"] = kept_calls
                cleaned.append(message)
            # else: drop the entire message (no tool_calls remain)
        else:
            cleaned.append(message)
    if pruned_tool_calls:
        LOG.info("normalized_chat_messages pruned_orphan_tool_calls=%s", pruned_tool_calls)
    return cleaned


def missing_tool_call_ids_from_error_detail(detail: str) -> set[str]:
    match = re.search(r"tool_call_ids did not have response messages:\s*([^;]+)", detail)
    if not match:
        return set()
    return {
        item.strip().strip("`'\"")
        for item in re.split(r"[\s,]+", match.group(1))
        if item.strip().strip("`'\"")
    }


def drop_tool_call_blocks_by_ids(
    messages: list[dict[str, Any]],
    missing_tool_call_ids: set[str],
) -> list[dict[str, Any]]:
    if not missing_tool_call_ids:
        return messages
    repaired: list[dict[str, Any]] = []
    dropped_blocks = 0
    dropped_tool_messages = 0
    index = 0
    while index < len(messages):
        message = messages[index]
        tool_call_ids = message_tool_call_ids(message)
        if message.get("role") != "assistant" or not tool_call_ids.intersection(missing_tool_call_ids):
            repaired.append(message)
            index += 1
            continue

        dropped_blocks += 1
        index += 1
        while index < len(messages) and messages[index].get("role") == "tool":
            dropped_tool_messages += 1
            index += 1

    if dropped_blocks == 0:
        fallback = drop_all_tool_call_blocks(messages)
        LOG.info(
            "repaired_missing_tool_call_blocks missing_tool_call_ids=%s dropped_blocks=0 fallback_drop_all_tool_blocks=true",
            ",".join(sorted(missing_tool_call_ids)),
        )
        return fallback

    LOG.info(
        "repaired_missing_tool_call_blocks missing_tool_call_ids=%s dropped_blocks=%s dropped_tool_messages=%s",
        ",".join(sorted(missing_tool_call_ids)),
        dropped_blocks,
        dropped_tool_messages,
    )
    return repaired


def drop_all_tool_call_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    dropped_blocks = 0
    dropped_tool_messages = 0
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "tool":
            dropped_tool_messages += 1
            index += 1
            continue

        if message.get("role") != "assistant" or not message_has_tool_calls(message):
            repaired.append(message)
            index += 1
            continue

        dropped_blocks += 1
        index += 1
        while index < len(messages) and messages[index].get("role") == "tool":
            dropped_tool_messages += 1
            index += 1

    LOG.info(
        "dropped_all_tool_call_blocks dropped_blocks=%s dropped_tool_messages=%s",
        dropped_blocks,
        dropped_tool_messages,
    )
    return repaired


def normalize_tool_call_ids_for_upstream(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    id_map: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    next_id = 1
    renamed_tool_calls = 0
    renamed_tool_messages = 0

    for message in messages:
        copied = dict(message)
        tool_calls = copied.get("tool_calls")
        if isinstance(tool_calls, list):
            normalized_tool_calls: list[Any] = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    normalized_tool_calls.append(call)
                    continue
                old_id = call.get("id") or call.get("call_id") or call.get("tool_call_id")
                if not old_id:
                    normalized_tool_calls.append(call)
                    continue
                old_id_text = str(old_id)
                new_id = id_map.get(old_id_text)
                if not new_id:
                    new_id = f"call_{next_id}"
                    next_id += 1
                    id_map[old_id_text] = new_id
                normalized_call = dict(call)
                normalized_call["id"] = new_id
                normalized_call.pop("call_id", None)
                normalized_call.pop("tool_call_id", None)
                normalized_tool_calls.append(normalized_call)
                if old_id_text != new_id:
                    renamed_tool_calls += 1
            copied["tool_calls"] = normalized_tool_calls

        tool_call_id = copied.get("tool_call_id")
        if tool_call_id:
            old_id_text = str(tool_call_id)
            new_id = id_map.get(old_id_text)
            if new_id:
                copied["tool_call_id"] = new_id
                if old_id_text != new_id:
                    renamed_tool_messages += 1

        normalized.append(copied)

    if renamed_tool_calls or renamed_tool_messages:
        LOG.info(
            "normalized_tool_call_ids renamed_tool_calls=%s renamed_tool_messages=%s",
            renamed_tool_calls,
            renamed_tool_messages,
        )
    return normalized


def build_chat_payload(payload: dict[str, Any], stream: bool) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    messages = normalize_tool_call_ids_for_upstream(
        normalize_chat_messages_for_upstream(responses_input_to_messages(payload))
    )
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
            tool_choice = responses_tool_choice_to_chat(payload.get("tool_choice"))
            if tool_choice is not None:
                chat_payload["tool_choice"] = tool_choice

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
    tool_name_aliases: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    custom_tool_names = custom_tool_names or set()
    tool_name_aliases = tool_name_aliases or {}
    output: list[dict[str, Any]] = []
    text = message.get("content") or ""
    reasoning = text_from_content(message.get("reasoning_content"))
    if reasoning:
        output.append(
            {
                "id": new_id("rs"),
                "type": "reasoning",
                "status": "completed",
                "summary": [{"type": "summary_text", "text": reasoning}],
            }
        )
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
        name = resolve_tool_call_name(tool_call["function"].get("name"), tool_name_aliases)
        tool_call["function"]["name"] = name
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


def response_output_to_chat_messages(
    output: list[dict[str, Any]],
    reasoning_content: str = "",
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []
    pending_text: list[str] = []

    for item in output:
        item_type = item.get("type")
        if item_type == "reasoning":
            for summary in item.get("summary") or []:
                summary_text = summary.get("text", "")
                if summary_text:
                    if not reasoning_content:
                        reasoning_content = summary_text
                    else:
                        reasoning_content += "\n" + summary_text
        elif item_type == "message":
            text = text_from_content(item.get("content"))
            if text:
                pending_text.append(text)
        elif item_type == "function_call":
            pending_tool_calls.append(response_function_call_to_chat_tool_call(item))
        elif item_type == "custom_tool_call":
            pending_tool_calls.append(response_custom_tool_call_to_chat_tool_call(item))

    if pending_text or pending_tool_calls or reasoning_content:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "\n".join(pending_text) if pending_text else None,
        }
        if pending_tool_calls:
            message["tool_calls"] = pending_tool_calls
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
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


def store_response(
    response_id: str,
    request_messages: list[dict[str, Any]],
    output: list[dict[str, Any]],
    reasoning_content: str = "",
) -> None:
    prune_response_store()
    remember_tool_call_reasoning(output, reasoning_content)
    messages = [*request_messages, *response_output_to_chat_messages(output, reasoning_content)]
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


def compact_text(value: str, limit: int = MAX_UPSTREAM_ERROR_DETAIL_CHARS) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def collect_error_fields(value: Any, prefix: str = "") -> list[str]:
    fields: list[str] = []
    if isinstance(value, dict):
        for key in ("message", "detail", "code", "type", "param"):
            item = value.get(key)
            if isinstance(item, (str, int, float, bool)):
                label = f"{prefix}{key}" if prefix else key
                fields.append(f"{label}={item}")
        for key in ("error", "errors"):
            item = value.get(key)
            if isinstance(item, (dict, list)):
                fields.extend(collect_error_fields(item, f"{key}."))
            elif isinstance(item, str):
                fields.append(f"{key}={item}")
    elif isinstance(value, list):
        for index, item in enumerate(value[:3]):
            fields.extend(collect_error_fields(item, f"{prefix}{index}."))
    elif isinstance(value, str):
        fields.append(value)
    return fields


def sanitized_upstream_error_from_text(status_code: int, text: str) -> str:
    detail = ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        detail = text
    else:
        fields = collect_error_fields(parsed)
        detail = "; ".join(fields) if fields else json.dumps(parsed, ensure_ascii=False)
    detail = compact_text(detail)
    return f"{upstream_error_detail(status_code)}: {detail}" if detail else upstream_error_detail(status_code)


def sanitized_upstream_error_from_response(response: httpx.Response) -> str:
    return sanitized_upstream_error_from_text(response.status_code, response.text)


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
        "upstream_tool_choice_mode": SETTINGS.upstream_tool_choice_mode,
        "response_language_instruction_configured": bool(SETTINGS.response_language_instruction),
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
            stream_response(
                request_id,
                chat_payload,
                request_messages,
                custom_tool_names,
                tool_name_aliases_for_chat_tools(chat_payload.get("tools")),
                started,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async with httpx.AsyncClient(timeout=SETTINGS.request_timeout_seconds) as client:
        for attempt in range(2):
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

            if upstream.status_code < 400:
                break

            detail = sanitized_upstream_error_from_response(upstream)
            missing_tool_call_ids = missing_tool_call_ids_from_error_detail(detail)
            if attempt == 0 and missing_tool_call_ids:
                chat_payload = {
                    **chat_payload,
                    "messages": drop_tool_call_blocks_by_ids(
                        chat_payload.get("messages") or [],
                        missing_tool_call_ids,
                    ),
                }
                continue

            record_failed_metrics()
            status_code = map_httpx_error(upstream.status_code)
            LOG.info(
                "request_done request_id=%s model=%s status=upstream_error upstream_status=%s detail=%s elapsed_ms=%d",
                request_id,
                model,
                upstream.status_code,
                detail,
                int((time.perf_counter() - started) * 1000),
            )
            raise HTTPException(status_code=status_code, detail=detail)

    data = upstream.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reasoning_content = text_from_content(message.get("reasoning_content"))
    output, output_text = chat_message_to_response_output(
        message,
        custom_tool_names,
        tool_name_aliases_for_chat_tools(chat_payload.get("tools")),
    )
    response_id = new_id("resp")
    response_json = build_response_json(
        response_id=response_id,
        model=model,
        output=output,
        output_text=output_text,
        usage=data.get("usage"),
    )
    store_response(response_id, request_messages, output, reasoning_content)
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
    tool_name_aliases: dict[str, str],
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
    reasoning_content = ""
    reasoning_item_id: str | None = None
    tool_items: dict[str, dict[str, Any]] = {}
    tool_call_order: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=SETTINGS.request_timeout_seconds) as client:
            for attempt in range(2):
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
                        error_body = await upstream.aread()
                        detail = sanitized_upstream_error_from_text(
                            upstream.status_code,
                            error_body.decode("utf-8", errors="replace"),
                        )
                        missing_tool_call_ids = missing_tool_call_ids_from_error_detail(detail)
                        if attempt == 0 and missing_tool_call_ids:
                            chat_payload = {
                                **chat_payload,
                                "messages": drop_tool_call_blocks_by_ids(
                                    chat_payload.get("messages") or [],
                                    missing_tool_call_ids,
                                ),
                            }
                            continue

                        record_failed_metrics()
                        yield sse(
                            "error",
                            {
                                "type": "error",
                                "code": str(upstream.status_code),
                                "message": detail,
                            },
                        )
                        LOG.info(
                            "request_done request_id=%s model=%s status=upstream_error upstream_status=%s detail=%s elapsed_ms=%d",
                            request_id,
                            model,
                            upstream.status_code,
                            detail,
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

                        reasoning_content_delta = delta.get("reasoning_content") or ""
                        if reasoning_content_delta:
                            if reasoning_item_id is None:
                                reasoning_item_id = new_id("rs")
                                reasoning_item = {
                                    "id": reasoning_item_id,
                                    "type": "reasoning",
                                    "status": "in_progress",
                                    "summary": [],
                                }
                                yield sse(
                                    "response.output_item.added",
                                    {"type": "response.output_item.added", "output_index": 0, "item": reasoning_item},
                                )
                            reasoning_content += str(reasoning_content_delta)

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
                                text_output_idx = 1 if reasoning_item_id is not None else 0
                                yield sse(
                                    "response.output_item.added",
                                    {"type": "response.output_item.added", "output_index": text_output_idx, "item": item},
                                )
                                yield sse(
                                    "response.content_part.added",
                                    {
                                        "type": "response.content_part.added",
                                        "item_id": text_item_id,
                                        "output_index": text_output_idx,
                                        "content_index": 0,
                                        "part": {"type": "output_text", "text": "", "annotations": []},
                                    },
                                )
                            text_output += content_delta
                            text_output_idx = 1 if reasoning_item_id is not None else 0
                            yield sse(
                                "response.output_text.delta",
                                {
                                    "type": "response.output_text.delta",
                                    "item_id": text_item_id,
                                    "output_index": text_output_idx,
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
                                name = resolve_tool_call_name(function.get("name") or "", tool_name_aliases)
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
                                output_index = (1 if reasoning_item_id is not None else 0) + (1 if text_item_id is not None else 0) + len(tool_call_order) - 1
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
                                existing["name"] = resolve_tool_call_name(function["name"], tool_name_aliases)
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
                                            "output_index": (1 if reasoning_item_id is not None else 0) + (1 if text_item_id is not None else 0) + tool_call_order.index(index),
                                            "delta": arg_delta,
                                        },
                                    )
                    break
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

    reasoning_offset = 1 if reasoning_item_id is not None else 0
    if reasoning_item_id is not None:
        reasoning_item = {
            "id": reasoning_item_id,
            "type": "reasoning",
            "status": "completed",
            "summary": [{"type": "summary_text", "text": reasoning_content}],
        }
        output.append(reasoning_item)
        yield sse(
            "response.output_item.done",
            {"type": "response.output_item.done", "output_index": 0, "item": reasoning_item},
        )

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
                "output_index": reasoning_offset,
                "content_index": 0,
                "text": text_output,
            },
        )
        yield sse(
            "response.content_part.done",
            {
                "type": "response.content_part.done",
                "item_id": text_item_id,
                "output_index": reasoning_offset,
                "content_index": 0,
                "part": {"type": "output_text", "text": text_output, "annotations": []},
            },
        )
        yield sse("response.output_item.done", {"type": "response.output_item.done", "output_index": reasoning_offset, "item": text_item})

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
    store_response(response_id, request_messages, output, reasoning_content)
    record_completed_metrics(text_output, output, None)
    yield sse("response.completed", {"type": "response.completed", "response": completed})
    LOG.info(
        "request_done request_id=%s response_id=%s model=%s status=completed elapsed_ms=%d",
        request_id,
        response_id,
        model,
        int((time.perf_counter() - started) * 1000),
    )
