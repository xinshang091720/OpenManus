import asyncio
from types import SimpleNamespace

from app.agent.toolcall import ToolCallAgent
from app.schema import AgentState, Memory, ToolChoice
from app.tool.tool_collection import ToolCollection


class ReplyOnlyLlm:
    async def ask_tool(self, **kwargs):
        return SimpleNamespace(content="final answer", tool_calls=[])


def test_reply_without_tool_call_finishes_one_agent_turn():
    agent = ToolCallAgent.model_construct(
        name="reply-only",
        description="",
        system_prompt="",
        next_step_prompt="",
        available_tools=ToolCollection(),
        tool_choices=ToolChoice.AUTO,
        tool_calls=[],
        state=AgentState.IDLE,
        memory=Memory(),
        llm=ReplyOnlyLlm(),
        special_tool_names=[],
        max_steps=20,
        current_step=0,
        max_observe=None,
        max_context_messages=24,
    )
    agent.update_memory("user", "hello")

    result = asyncio.run(agent.run())

    assert result == "Step 1: final answer"
    assert agent.current_step == 0
    assert agent.state == AgentState.IDLE


def test_system_memory_message_accepts_the_default_image_argument():
    """Runtime continuation hints are system messages without image payloads."""
    agent = ToolCallAgent.model_construct(
        name="system-memory",
        description="",
        system_prompt="",
        next_step_prompt="",
        available_tools=ToolCollection(),
        tool_choices=ToolChoice.AUTO,
        tool_calls=[],
        state=AgentState.IDLE,
        memory=Memory(),
        llm=ReplyOnlyLlm(),
        special_tool_names=[],
        max_steps=20,
        current_step=0,
        max_observe=None,
        max_context_messages=24,
    )

    agent.update_memory("system", "continue the current Revit model")

    message = agent.memory.messages[-1]
    assert message.role == "system"
    assert message.content == "continue the current Revit model"
    assert message.base64_image is None
