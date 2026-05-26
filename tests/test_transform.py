import os
import unittest

os.environ.setdefault("CODEX_GLM_PROXY_KEY", "test-proxy-key")
os.environ.setdefault("ENABLE_TOOL_CALLS", "true")

from proxy_app.main import (  # noqa: E402
    build_chat_payload,
    chat_message_to_response_output,
    input_item_to_messages,
    metrics_snapshot,
    record_completed_metrics,
    record_request_metrics,
)


class TransformTests(unittest.TestCase):
    def test_string_input_to_user_message(self) -> None:
        payload, messages, custom_tool_names = build_chat_payload({"model": "glm-5.1", "input": "hello"}, stream=False)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(messages, payload["messages"])
        self.assertEqual(custom_tool_names, set())

    def test_instructions_become_system_message(self) -> None:
        payload, _, _ = build_chat_payload(
            {"model": "glm-5.1", "instructions": "be terse", "input": "hello"},
            stream=False,
        )
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "be terse"})

    def test_message_content_array_to_text(self) -> None:
        messages = input_item_to_messages(
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "a"},
                    {"type": "input_text", "text": "b"},
                ],
            }
        )
        self.assertEqual(messages, [{"role": "user", "content": "a\nb"}])

    def test_function_call_output_to_tool_message(self) -> None:
        messages = input_item_to_messages(
            {"type": "function_call_output", "call_id": "call_1", "output": "done"}
        )
        self.assertEqual(messages, [{"role": "tool", "tool_call_id": "call_1", "content": "done"}])

    def test_chat_tool_call_to_response_function_call(self) -> None:
        output, text = chat_message_to_response_output(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"},
                    }
                ],
            }
        )
        self.assertEqual(text, "")
        self.assertEqual(output[0]["type"], "function_call")
        self.assertEqual(output[0]["call_id"], "call_1")
        self.assertEqual(output[0]["name"], "get_time")

    def test_custom_tool_to_chat_function_tool(self) -> None:
        payload, _, custom_tool_names = build_chat_payload(
            {
                "model": "glm-5.1",
                "input": "hello",
                "tools": [
                    {
                        "type": "custom",
                        "name": "shell",
                        "description": "Run a shell command.",
                    }
                ],
            },
            stream=False,
        )
        self.assertEqual(custom_tool_names, {"shell"})
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "shell")
        self.assertEqual(payload["tools"][0]["function"]["parameters"]["required"], ["input"])

    def test_chat_tool_call_to_response_custom_tool_call(self) -> None:
        output, text = chat_message_to_response_output(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "shell", "arguments": '{"input":"pwd"}'},
                    }
                ],
            },
            custom_tool_names={"shell"},
        )
        self.assertEqual(text, "")
        self.assertEqual(output[0]["type"], "custom_tool_call")
        self.assertEqual(output[0]["call_id"], "call_1")
        self.assertEqual(output[0]["name"], "shell")
        self.assertEqual(output[0]["input"], "pwd")

    def test_custom_tool_call_output_to_tool_message(self) -> None:
        messages = input_item_to_messages(
            {"type": "custom_tool_call_output", "call_id": "call_1", "output": "done"}
        )
        self.assertEqual(messages, [{"role": "tool", "tool_call_id": "call_1", "content": "done"}])

    def test_web_search_tool_is_ignored(self) -> None:
        payload, _, custom_tool_names = build_chat_payload(
            {
                "model": "glm-5.1",
                "input": "hello",
                "tools": [{"type": "web_search"}],
            },
            stream=False,
        )
        self.assertNotIn("tools", payload)
        self.assertEqual(custom_tool_names, set())

    def test_metrics_count_text_chars_and_upstream_usage(self) -> None:
        before = metrics_snapshot()
        record_request_metrics([{"role": "user", "content": "hello"}])
        record_completed_metrics(
            "world",
            [{"type": "function_call", "arguments": '{"x":1}'}],
            {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        )
        after = metrics_snapshot()

        self.assertEqual(after["requests"]["started"] - before["requests"]["started"], 1)
        self.assertEqual(after["requests"]["completed"] - before["requests"]["completed"], 1)
        self.assertEqual(after["chars"]["request_text"] - before["chars"]["request_text"], 5)
        self.assertEqual(after["chars"]["response_text"] - before["chars"]["response_text"], 5)
        self.assertEqual(after["chars"]["response_tool_calls"] - before["chars"]["response_tool_calls"], 7)
        self.assertEqual(after["upstream_usage_tokens"]["prompt"] - before["upstream_usage_tokens"]["prompt"], 3)
        self.assertEqual(after["upstream_usage_tokens"]["completion"] - before["upstream_usage_tokens"]["completion"], 4)
        self.assertEqual(after["upstream_usage_tokens"]["total"] - before["upstream_usage_tokens"]["total"], 7)


if __name__ == "__main__":
    unittest.main()
