from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def base_payload(model: str, stream: bool, include_tools: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "stream": stream,
        "temperature": 0,
    }
    if include_tools:
        payload["messages"] = [
            {
                "role": "user",
                "content": "Call get_time with no arguments. Do not answer in natural language.",
            }
        ]
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Return the current local time.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            }
        ]
        payload["tool_choice"] = {"type": "function", "function": {"name": "get_time"}}
    return payload


def post_non_stream(client: httpx.Client, url: str, key: str, model: str, include_tools: bool) -> tuple[bool, str]:
    response = client.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=base_payload(model, stream=False, include_tools=include_tools),
    )
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    if include_tools:
        ok = bool(message.get("tool_calls"))
        return ok, "tool_calls present" if ok else "no tool_calls in message"
    ok = bool(message.get("content"))
    return ok, "content present" if ok else "no content in message"


def post_stream(client: httpx.Client, url: str, key: str, model: str, include_tools: bool) -> tuple[bool, str]:
    saw_text = False
    saw_tool = False
    with client.stream(
        "POST",
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=base_payload(model, stream=True, include_tools=include_tools),
    ) as response:
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}"
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
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
            if delta.get("content"):
                saw_text = True
            if delta.get("tool_calls"):
                saw_tool = True
    if include_tools:
        return saw_tool, "stream tool_calls present" if saw_tool else "no stream tool_calls"
    return saw_text, "stream content present" if saw_text else "no stream content"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Tencent TokenHub Chat Completions compatibility.")
    parser.add_argument("--url", default=os.getenv("TOKENHUB_BASE_URL", "https://tokenhub.tencentmaas.com/plan/v3/chat/completions"))
    parser.add_argument("--model", default=os.getenv("TOKENHUB_MODEL", "glm-5.1"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PROXY_REQUEST_TIMEOUT_SECONDS", "600")))
    args = parser.parse_args()

    key = os.getenv("TOKENHUB_API_KEY")
    if not key:
        print("TOKENHUB_API_KEY is required", file=sys.stderr)
        return 2

    checks = [
        ("non_stream_text", lambda c: post_non_stream(c, args.url, key, args.model, include_tools=False)),
        ("stream_text", lambda c: post_stream(c, args.url, key, args.model, include_tools=False)),
        ("non_stream_tool_calls", lambda c: post_non_stream(c, args.url, key, args.model, include_tools=True)),
        ("stream_tool_calls", lambda c: post_stream(c, args.url, key, args.model, include_tools=True)),
    ]

    results: dict[str, bool] = {}
    with httpx.Client(timeout=args.timeout) as client:
        for name, check in checks:
            try:
                ok, detail = check(client)
            except httpx.HTTPError as exc:
                ok, detail = False, exc.__class__.__name__
            results[name] = ok
            print(f"{name}: {'PASS' if ok else 'FAIL'} - {detail}")

    tool_ok = results.get("non_stream_tool_calls", False) and results.get("stream_tool_calls", False)
    print()
    if tool_ok:
        print("Tool-call probe passed. Start proxy with:")
        print('$env:ENABLE_TOOL_CALLS = "true"')
    else:
        print("Tool-call probe failed. Leave ENABLE_TOOL_CALLS=false; Codex coding-agent use will not be reliable.")

    return 0 if results.get("non_stream_text") and results.get("stream_text") else 1


if __name__ == "__main__":
    raise SystemExit(main())
