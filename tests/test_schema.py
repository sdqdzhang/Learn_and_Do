from core.schema import ChatMessage, MessageRole


def test_tool_message_renders_as_user_context_for_openai_compat() -> None:
    msg = ChatMessage(
        role=MessageRole.TOOL,
        content="[工具 python_repl 成功]\n42",
        tool_call_id="tc-123",
    )

    rendered = msg.to_openai()

    assert rendered["role"] == "user"
    assert "tool_call_id" not in rendered
    assert "tc-123" in rendered["content"]
    assert "42" in rendered["content"]

