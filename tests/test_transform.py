import os
import unittest

os.environ.setdefault("CODEX_GLM_PROXY_KEY", "test-proxy-key")
os.environ.setdefault("ENABLE_TOOL_CALLS", "true")

from proxy_app.main import (  # noqa: E402
    SETTINGS,
    build_chat_payload,
    chat_message_to_response_output,
    drop_tool_call_blocks_by_ids,
    input_item_to_messages,
    missing_tool_call_ids_from_error_detail,
    normalize_chat_messages_for_upstream,
    normalize_tool_call_ids_for_upstream,
    metrics_snapshot,
    record_completed_metrics,
    record_request_metrics,
    response_output_to_chat_messages,
    responses_input_to_messages,
    store_response,
    tool_name_aliases_for_chat_tools,
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

    def test_response_language_instruction_is_prepended(self) -> None:
        original_instruction = SETTINGS.response_language_instruction
        try:
            object.__setattr__(SETTINGS, "response_language_instruction", "Reply in Simplified Chinese.")
            payload, _, _ = build_chat_payload(
                {"model": "glm-5.1", "instructions": "be terse", "input": "hello"},
                stream=False,
            )
        finally:
            object.__setattr__(SETTINGS, "response_language_instruction", original_instruction)

        self.assertEqual(payload["messages"][0], {"role": "system", "content": "Reply in Simplified Chinese."})
        self.assertEqual(payload["messages"][1], {"role": "system", "content": "be terse"})

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

    def test_normalize_chat_messages_keeps_complete_tool_call_block(self) -> None:
        messages = normalize_chat_messages_for_upstream(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "done"},
                {"role": "user", "content": "continue"},
            ]
        )

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[1]["role"], "tool")

    def test_normalize_chat_messages_accepts_tool_call_id_alias(self) -> None:
        messages = normalize_chat_messages_for_upstream(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "tool_call_id": "call_1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "done"},
            ]
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[1]["role"], "tool")

    def test_normalize_chat_messages_drops_incomplete_tool_call_block(self) -> None:
        messages = normalize_chat_messages_for_upstream(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "user", "content": "continue"},
            ]
        )

        self.assertEqual(messages, [{"role": "user", "content": "continue"}])

    def test_normalize_chat_messages_drops_tool_call_block_without_ids(self) -> None:
        messages = normalize_chat_messages_for_upstream(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "done"},
                {"role": "user", "content": "continue"},
            ]
        )

        self.assertEqual(messages, [{"role": "user", "content": "continue"}])

    def test_normalize_chat_messages_drops_orphan_tool_message(self) -> None:
        messages = normalize_chat_messages_for_upstream(
            [
                {"role": "tool", "tool_call_id": "call_1", "content": "done"},
                {"role": "user", "content": "continue"},
            ]
        )

        self.assertEqual(messages, [{"role": "user", "content": "continue"}])

    def test_missing_tool_call_ids_can_repair_incomplete_history(self) -> None:
        detail = (
            "TokenHub upstream request failed: error.message=An assistant message with "
            "'tool_calls' must be followed by tool messages responding to each "
            "'tool_call_id', The following tool_call_ids did not have response messages: "
            "call_1; error.code=invalid_request_error"
        )
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "tool_call_id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {"role": "user", "content": "continue"},
        ]

        repaired = drop_tool_call_blocks_by_ids(
            messages,
            missing_tool_call_ids_from_error_detail(detail),
        )

        self.assertEqual(repaired, [{"role": "user", "content": "continue"}])

    def test_missing_tool_call_repair_falls_back_to_drop_all_tool_blocks(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "other_call",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "other_call", "content": "done"},
            {"role": "user", "content": "continue"},
        ]

        repaired = drop_tool_call_blocks_by_ids(messages, {"missing_call"})

        self.assertEqual(repaired, [{"role": "user", "content": "continue"}])

    def test_normalize_tool_call_ids_for_upstream_rewrites_matching_tool_messages(self) -> None:
        messages = normalize_tool_call_ids_for_upstream(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "019e3a5adf010f55b528fcf572023c0e",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "019e3a5adf010f55b528fcf572023c0e",
                    "content": "done",
                },
            ]
        )

        self.assertEqual(messages[0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(messages[1]["tool_call_id"], "call_1")

    def test_build_chat_payload_uses_short_tool_call_ids_for_history(self) -> None:
        payload, _, _ = build_chat_payload(
            {
                "model": "minimax-m-2-7",
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "019e3a5adf010f55b528fcf572023c0e",
                        "name": "read_file",
                        "arguments": "{}",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "019e3a5adf010f55b528fcf572023c0e",
                        "output": "done",
                    },
                    {"role": "user", "content": "continue"},
                ],
            },
            stream=False,
        )

        self.assertEqual(payload["messages"][0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(payload["messages"][1]["tool_call_id"], "call_1")

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

    def test_shell_tool_call_is_aliased_to_shell_command_when_available(self) -> None:
        aliases = tool_name_aliases_for_chat_tools(
            [{"type": "function", "function": {"name": "shell_command"}}]
        )
        output, _ = chat_message_to_response_output(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "shell", "arguments": '{"cmd":"pwd"}'},
                    }
                ],
            },
            tool_name_aliases=aliases,
        )

        self.assertEqual(output[0]["type"], "function_call")
        self.assertEqual(output[0]["name"], "shell_command")

    def test_shell_tool_call_is_not_aliased_when_shell_is_available(self) -> None:
        aliases = tool_name_aliases_for_chat_tools(
            [
                {"type": "function", "function": {"name": "shell"}},
                {"type": "function", "function": {"name": "shell_command"}},
            ]
        )
        self.assertEqual(aliases, {})

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

    def test_namespace_tool_is_ignored(self) -> None:
        payload, _, custom_tool_names = build_chat_payload(
            {
                "model": "glm-5.1",
                "input": "hello",
                "tools": [{"type": "namespace", "name": "browser"}],
            },
            stream=False,
        )
        self.assertNotIn("tools", payload)
        self.assertEqual(custom_tool_names, set())

    def test_omit_forced_tool_choice_mode_drops_forced_function_choice(self) -> None:
        original_mode = SETTINGS.upstream_tool_choice_mode
        try:
            object.__setattr__(SETTINGS, "upstream_tool_choice_mode", "omit_forced")
            payload, _, _ = build_chat_payload(
                {
                    "model": "glm-5.1",
                    "input": "hello",
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_time",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                    "tool_choice": {"type": "function", "name": "get_time"},
                },
                stream=False,
            )
        finally:
            object.__setattr__(SETTINGS, "upstream_tool_choice_mode", original_mode)

        self.assertIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

    def test_omit_forced_tool_choice_mode_drops_required_choice(self) -> None:
        original_mode = SETTINGS.upstream_tool_choice_mode
        try:
            object.__setattr__(SETTINGS, "upstream_tool_choice_mode", "omit_forced")
            payload, _, _ = build_chat_payload(
                {
                    "model": "glm-5.1",
                    "input": "hello",
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_time",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                    "tool_choice": "required",
                },
                stream=False,
            )
        finally:
            object.__setattr__(SETTINGS, "upstream_tool_choice_mode", original_mode)

        self.assertIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

    def test_passthrough_tool_choice_mode_keeps_forced_function_choice(self) -> None:
        original_mode = SETTINGS.upstream_tool_choice_mode
        try:
            object.__setattr__(SETTINGS, "upstream_tool_choice_mode", "passthrough")
            payload, _, _ = build_chat_payload(
                {
                    "model": "glm-5.1",
                    "input": "hello",
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_time",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                    "tool_choice": {"type": "function", "name": "get_time"},
                },
                stream=False,
            )
        finally:
            object.__setattr__(SETTINGS, "upstream_tool_choice_mode", original_mode)

        self.assertEqual(payload["tool_choice"], {"type": "function", "function": {"name": "get_time"}})

    def test_response_output_to_chat_messages_preserves_reasoning_content(self) -> None:
        messages = response_output_to_chat_messages(
            [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                }
            ],
            reasoning_content="hidden thinking state",
        )

        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[0]["content"], None)
        self.assertEqual(messages[0]["reasoning_content"], "hidden thinking state")
        self.assertEqual(messages[0]["tool_calls"][0]["id"], "call_1")

    def test_function_call_input_restores_reasoning_content_by_call_id(self) -> None:
        store_response(
            "resp_test_reasoning",
            [{"role": "user", "content": "read README"}],
            [
                {
                    "type": "function_call",
                    "call_id": "call_reasoning_1",
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                }
            ],
            reasoning_content="hidden thinking state",
        )

        messages = input_item_to_messages(
            {
                "type": "function_call",
                "call_id": "call_reasoning_1",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
            }
        )

        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[0]["reasoning_content"], "hidden thinking state")
        self.assertEqual(messages[0]["tool_calls"][0]["id"], "call_reasoning_1")

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

    def test_chat_message_to_response_output_includes_reasoning_item(self) -> None:
        output, text = chat_message_to_response_output(
            {"role": "assistant", "content": "hello", "reasoning_content": "thinking..."},
        )
        self.assertEqual(text, "hello")
        reasoning_items = [i for i in output if i["type"] == "reasoning"]
        self.assertEqual(len(reasoning_items), 1)
        self.assertEqual(reasoning_items[0]["summary"][0]["text"], "thinking...")
        message_items = [i for i in output if i["type"] == "message"]
        self.assertEqual(len(message_items), 1)

    def test_chat_message_to_response_output_no_reasoning_when_empty(self) -> None:
        output, text = chat_message_to_response_output(
            {"role": "assistant", "content": "hello"},
        )
        reasoning_items = [i for i in output if i["type"] == "reasoning"]
        self.assertEqual(len(reasoning_items), 0)

    def test_response_output_to_chat_messages_extracts_reasoning_from_item(self) -> None:
        messages = response_output_to_chat_messages(
            [
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "my thoughts"}],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "answer"}],
                },
            ],
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[0]["content"], "answer")
        self.assertEqual(messages[0]["reasoning_content"], "my thoughts")

    def test_input_item_to_messages_preserves_reasoning_content_in_assistant_message(self) -> None:
        messages = input_item_to_messages(
            {
                "type": "message",
                "role": "assistant",
                "content": "hello",
                "reasoning_content": "thinking step by step",
            }
        )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "assistant")
        self.assertEqual(messages[0]["content"], "hello")
        self.assertEqual(messages[0]["reasoning_content"], "thinking step by step")

    def test_input_item_to_messages_reasoning_item_merges_to_previous_assistant(self) -> None:
        payload = {
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "hi"},
                {"type": "message", "role": "assistant", "content": "hello"},
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "I thought about it"}],
                },
            ],
        }
        messages = responses_input_to_messages(payload)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0]["reasoning_content"], "I thought about it")

    def test_consecutive_function_calls_are_merged_into_one_assistant_message(self) -> None:
        """Multiple function_call input items should merge into one assistant message."""
        payload = {
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "do stuff"},
                {"type": "function_call", "call_id": "call_A", "name": "read_file", "arguments": "{}"},
                {"type": "function_call", "call_id": "call_B", "name": "write_file", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_A", "output": "file content"},
                {"type": "function_call_output", "call_id": "call_B", "output": "written"},
                {"type": "message", "role": "user", "content": "what next?"},
            ],
        }
        messages = responses_input_to_messages(payload)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1, "consecutive function_calls should merge into one assistant message")
        self.assertEqual(len(assistant_msgs[0]["tool_calls"]), 2)
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2)

    def test_function_call_with_text_not_merged(self) -> None:
        """An assistant message with text content should NOT merge with the next."""
        payload = {
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "go"},
                {"type": "message", "role": "assistant", "content": "thinking..."},
                {"type": "function_call", "call_id": "call_A", "name": "read", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_A", "output": "ok"},
            ],
        }
        messages = responses_input_to_messages(payload)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        # "thinking..." assistant + function_call assistant (separate because "thinking..." has content)
        self.assertGreaterEqual(len(assistant_msgs), 2)

    def test_reasoning_roundtrip_preserves_content(self) -> None:
        """Full round-trip: DeepSeek response -> Responses output -> Codex echoes back -> Chat messages."""
        # Step 1: DeepSeek returns a message with reasoning_content
        output, _ = chat_message_to_response_output(
            {"role": "assistant", "content": "result", "reasoning_content": "deep thoughts"},
        )
        # Step 2: response_output_to_chat_messages should reconstruct reasoning_content
        messages = response_output_to_chat_messages(output)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["reasoning_content"], "deep thoughts")
        self.assertEqual(messages[0]["content"], "result")

        # Step 3: Codex sends back the output as input items
        payload = {
            "model": "deepseek-v4-pro",
            "input": [
                {"type": "message", "role": "user", "content": "question"},
            ]
            + output,
        }
        messages2 = responses_input_to_messages(payload)
        assistant_msgs = [m for m in messages2 if m["role"] == "assistant"]
        self.assertEqual(len(assistant_msgs), 1)
        self.assertEqual(assistant_msgs[0]["reasoning_content"], "deep thoughts")
        self.assertEqual(assistant_msgs[0]["content"], "result")


if __name__ == "__main__":
    unittest.main()
