import asyncio
import json

from fastapi.testclient import TestClient
import pytest

from app.api.runtime import (
    ConversationTopicGenerator,
    CreateRunRequest,
    HistoryMessage,
    RuntimeManager,
    RuntimeManus,
    create_runtime_app,
)
from app.schema import AgentState, Memory, Message, ToolCall
from run_agent_runtime import bind_runtime_socket


class FakeAgent:
    def __init__(self, sink, gate=None, completed=None):
        self.sink = sink
        self.gate = gate
        self.completed = completed
        self.memory = Memory()

    def update_memory(self, role, content):
        self.memory.add_message(Message(role=role, content=content))

    async def run(self, request):
        await self.sink("tool_started", {"tool": "fake_tool"})
        if self.gate:
            await self.gate.wait()
        self.memory.add_message(Message.assistant_message(f"finished: {request}"))
        await self.sink("tool_completed", {"tool": "fake_tool", "success": True})
        if self.completed is not None:
            self.completed.append(request)


def runtime_request(conversation_id="conversation-1"):
    return CreateRunRequest(
        request_id="request-1",
        conversation_id=conversation_id,
        user_message="open model",
        history=[{"role": "user", "content": "hello"}],
    )


def test_request_id_is_optional_and_does_not_define_context():
    request = CreateRunRequest(
        conversation_id="session-group-42",
        user_message="继续质检",
        history=[{"role": "assistant", "content": "IFC 已导出，请加载后回复已准备好"}],
    )
    assert request.request_id is None
    assert request.conversation_id == "session-group-42"
    assert request.history[0].role == "assistant"


def test_runtime_manager_faithfully_loads_history_into_agent_memory():
    recorded_agent = None

    async def scenario():
        nonlocal recorded_agent

        async def factory(sink, cancel_checker):
            nonlocal recorded_agent
            recorded_agent = FakeAgent(sink)
            return recorded_agent

        manager = RuntimeManager(agent_factory=factory)
        request = CreateRunRequest(
            conversation_id="conv-history-1",
            user_message="继续",
            history=[
                {"role": "user", "content": "请打开建筑模型并自检"},
                {"role": "assistant", "content": "IFC 已导出至 C:\\result.ifc，请确认加载。"},
            ],
        )
        run = await manager.create_run(request)
        await run.task

    asyncio.run(scenario())
    assert recorded_agent is not None
    # Verify that the history messages were faithfully recorded into agent memory
    assert len(recorded_agent.memory.messages) >= 2
    assert recorded_agent.memory.messages[0].role == "user"
    assert recorded_agent.memory.messages[0].content == "请打开建筑模型并自检"
    assert recorded_agent.memory.messages[1].role == "assistant"
    assert "C:\\result.ifc" in recorded_agent.memory.messages[1].content


def test_runtime_health_requires_bearer_token():
    app = create_runtime_app("runtime-token", RuntimeManager())
    client = TestClient(app)

    assert client.get("/api/v1/health").status_code == 401
    assert client.get(
        "/api/v1/health", headers={"Authorization": "Bearer runtime-token"}
    ).json()["revit_mcp_mode"] == "stdio"


def test_runtime_generates_a_conversation_topic_with_bearer_token():
    class FakeTopicGenerator:
        async def generate(self, messages):
            assert [message.role for message in messages] == ["user", "assistant"]
            return "美国高利率与房贷分析"

    app = create_runtime_app(
        "runtime-token", RuntimeManager(), topic_generator=FakeTopicGenerator()
    )
    client = TestClient(app)
    payload = {
        "messages": [
            {"role": "user", "content": "美国高利率对房贷有什么影响？"},
            {"role": "assistant", "content": "会提高借款成本。"},
        ]
    }

    assert client.post("/api/v1/conversations/topic", json=payload).status_code == 401
    response = client.post(
        "/api/v1/conversations/topic",
        json=payload,
        headers={"Authorization": "Bearer runtime-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"topic": "美国高利率与房贷分析"}


def test_conversation_topic_generator_normalizes_and_limits_llm_output():
    class FakeLLM:
        async def ask(self, *args, **kwargs):
            assert kwargs["stream"] is False
            assert kwargs["temperature"] == 0.2
            assert kwargs["enable_thinking"] is False
            return '  "这是一个超过三十个字符的会话主题，用于验证接口返回值会被安全地截断"  '

    topic = asyncio.run(
        ConversationTopicGenerator(FakeLLM()).generate(
            [
                HistoryMessage(role="user", content="请总结一下。"),
            ]
        )
    )
    assert topic == "这是一个超过三十个字符的会话主题，用于验证接口返回值会被安全"


def test_run_events_are_streamed_in_order():
    async def scenario():
        async def factory(sink, cancel_checker):
            return FakeAgent(sink)

        manager = RuntimeManager(agent_factory=factory)
        run = await manager.create_run(runtime_request())
        await run.task
        return [event.event async for event in manager.stream_events(run.run_id)]

    assert asyncio.run(scenario()) == [
        "run_queued",
        "run_started",
        "tool_started",
        "tool_completed",
        "assistant_message",
        "run_completed",
    ]


def test_same_conversation_runs_queue_and_cancel_before_execution():
    async def scenario():
        gate = asyncio.Event()
        started = asyncio.Event()
        completed = []

        async def factory(sink, cancel_checker):
            class StartedFakeAgent(FakeAgent):
                async def run(self, request):
                    started.set()
                    await super().run(request)

            return StartedFakeAgent(sink, gate=gate, completed=completed)

        manager = RuntimeManager(agent_factory=factory)
        first = await manager.create_run(runtime_request())
        await started.wait()
        second = await manager.create_run(runtime_request())
        await manager.cancel_run(second.run_id)
        immediate_events = [event.event for event in second.events]
        gate.set()
        await asyncio.gather(first.task, second.task)
        second_events = [event.event async for event in manager.stream_events(second.run_id)]
        return completed, immediate_events, second_events

    completed, immediate_events, second_events = asyncio.run(scenario())
    assert completed == ["open model"]
    assert immediate_events == ["run_queued", "run_cancelled"]
    assert second_events == ["run_queued", "run_cancelled"]


def test_runtime_rejects_non_chat_history_and_relative_attachments():
    app = create_runtime_app("runtime-token", RuntimeManager())
    client = TestClient(app)
    headers = {"Authorization": "Bearer runtime-token"}
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "request_id": "request-1",
            "conversation_id": "conversation-1",
            "user_message": "hello",
            "history": [{"role": "tool", "content": "not allowed"}],
        },
    )
    assert response.status_code == 422
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "request_id": "request-1",
            "conversation_id": "conversation-1",
            "user_message": "hello",
            "attachments": [{"path": "relative.rvt", "type": "revit_model"}],
        },
    )
    assert response.status_code == 422


def test_runtime_returns_not_found_for_unknown_run_events():
    app = create_runtime_app("runtime-token", RuntimeManager())
    client = TestClient(app)

    response = client.get(
        "/api/v1/runs/missing/events",
        headers={"Authorization": "Bearer runtime-token"},
    )
    assert response.status_code == 404


def test_runtime_port_binding_detects_an_existing_listener():
    first = bind_runtime_socket("127.0.0.1", 0)
    try:
        port = first.getsockname()[1]
        try:
            bind_runtime_socket("127.0.0.1", port)
            assert False, "expected occupied runtime port to fail"
        except RuntimeError as error:
            assert "port_in_use" in str(error)
    finally:
        first.close()


def test_runtime_revit_progress_uses_user_facing_business_summaries():
    assert "清除" in RuntimeManus._tool_summary("mcp_revit_local_revit_clear_parameters")
    assert "匹配" in RuntimeManus._tool_summary(
        "mcp_revit_local_revit_assign_ifc_identifiers"
    )
    assert RuntimeManus._tool_summary("revit_open_file", completed=True).startswith("已完成")
    room_summary = RuntimeManus._tool_summary("revit_create_and_name_ar_rooms")
    assert "DWG" in room_summary
    assert "AutoCAD" not in room_summary
    assert "天正" not in room_summary


def test_runtime_emits_periodic_revit_progress_without_assistant_message(monkeypatch):
    async def slow_tool(self, command):
        await asyncio.sleep(0.01)
        return "completed"

    monkeypatch.setattr("app.api.runtime.Manus.execute_tool", slow_tool)

    async def scenario():
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = RuntimeManus(event_sink=sink, cancel_checker=lambda: False)
        agent.tool_progress_interval_seconds = 0.001
        command = ToolCall(
            id="call-progress",
            function={"name": "mcp_revit_local_revit_export_ifc", "arguments": "{}"},
        )
        await agent.execute_tool(command)
        return events

    events = asyncio.run(scenario())
    progress = [data for event, data in events if event == "tool_progress"]
    assert progress
    assert set(progress[0]) == {"tool", "summary", "elapsed_seconds"}
    assert not any(event == "assistant_message" for event, _ in events)


def test_runtime_cancellation_prevents_the_next_tool_from_starting():
    async def scenario():
        cancelled = asyncio.Event()
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = RuntimeManus(event_sink=sink, cancel_checker=cancelled.is_set)
        command = ToolCall(
            id="call-1",
            function={"name": "mcp_revit_local_revit_assign_ifc_identifiers", "arguments": "{}"},
        )
        cancelled.set()
        result = await asyncio.wait_for(agent.execute_tool(command), timeout=2)
        return result, events

    result, events = asyncio.run(scenario())
    assert result.startswith("Error: Run cancelled")
    assert events == []


def test_runtime_converts_ask_human_to_sse_instead_of_terminal_input():
    async def scenario():
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = RuntimeManus(event_sink=sink, cancel_checker=lambda: False)
        command = ToolCall(
            id="call-1",
            function={
                "name": "ask_human",
                "arguments": '{"inquire":"请确认 Revit 插件是否已启动。"}',
            },
        )
        result = await agent.execute_tool(command)
        return result, events, agent.state

    result, events, state = asyncio.run(scenario())
    assert result == "User input requested through the Runtime SSE event."
    assert state == AgentState.FINISHED
    assert [event for event, _ in events] == [
        "tool_started",
        "assistant_message",
        "tool_completed",
    ]
    assert events[1][1] == {
        "phase": "input_required",
        "content": "请确认 Revit 插件是否已启动。",
    }


def test_runtime_turns_ambiguous_application_selection_into_sse_input_request():
    observation = 'Observed output of cmd `windows_open_application` executed:\n{"status":"selection_required","message":"请选择应用","candidates":[{"display_name":"App 2024","display_version":"2024","executable":"C:/App.exe"}]}'
    question = RuntimeManus._selection_required("windows_open_application", observation)
    assert question is not None
    assert "请选择应用" in question
    assert "App 2024" in question


def test_runtime_turns_ambiguous_revit_selection_into_sse_input_request():
    observation = 'Observed output of cmd `mcp_revit_local_revit_launch_application` executed:\n{"status":"selection_required","message":"请选择 Revit","candidates":[]}'
    assert RuntimeManus._selection_required("mcp_revit_local_revit_launch_application", observation) == "请选择 Revit"


def test_runtime_turns_existing_revit_into_sse_input_request():
    observation = 'Observed output of cmd `mcp_revit_local_revit_launch_versioned_model` executed:\n{"status":"user_action_required","message":"请在现有 Revit 中打开目标模型"}'
    assert RuntimeManus._input_required(
        "mcp_revit_local_revit_launch_versioned_model", observation
    ) == "请在现有 Revit 中打开目标模型"


def test_user_action_required_is_not_reported_as_tool_success(monkeypatch):
    observation = (
        'Observed output of cmd `mcp_revit_local_ensure_autocad_running` executed:\n'
        '{"status":"user_action_required","message":"请手动处理 AutoCAD 启动窗口"}'
    )

    async def manual_action(self, command):
        return observation

    monkeypatch.setattr("app.api.runtime.Manus.execute_tool", manual_action)

    async def scenario():
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = RuntimeManus(event_sink=sink, cancel_checker=lambda: False)
        command = ToolCall(
            id="cad-open",
            function={
                "name": "mcp_revit_local_ensure_autocad_running",
                "arguments": "{}",
            },
        )
        await agent.execute_tool(command)
        return events

    events = asyncio.run(scenario())
    completed = [data for event, data in events if event == "tool_completed"][-1]
    assert completed["success"] is False
    assert completed["summary"] == "请手动处理 AutoCAD 启动窗口"


def test_runtime_recognizes_legacy_json_tool_error():
    assert RuntimeManus._tool_observation_error('{"error":"desktop failed"}') == (
        "Error: desktop failed"
    )
    assert RuntimeManus._tool_observation_error(
        'Observed output:\n{"error":"desktop failed"}'
    ) == "Error: desktop failed"


def test_runtime_recognizes_mcp_error_inside_observation_wrapper():
    observation = (
        "Observed output of cmd `mcp_revit_local_revit_run_ifc_assignment` executed:\n"
        "Error: OneClickIdentifier returned HTTP 504"
    )
    assert RuntimeManus._tool_observation_error(observation) == (
        "Error: OneClickIdentifier returned HTTP 504"
    )


def test_failed_revit_write_finishes_run_and_blocks_sibling_write(monkeypatch):
    calls = []

    async def failed_tool(self, command):
        calls.append(command.function.name)
        return "Observed output:\nError: Revit final state unknown"

    monkeypatch.setattr("app.api.runtime.Manus.execute_tool", failed_tool)

    async def scenario():
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = RuntimeManus(event_sink=sink, cancel_checker=lambda: False)
        first = ToolCall(
            id="write-1",
            function={"name": "mcp_revit_local_revit_run_ifc_assignment", "arguments": "{}"},
        )
        second = ToolCall(
            id="write-2",
            function={"name": "mcp_revit_local_revit_save_as", "arguments": "{}"},
        )
        first_result = await agent.execute_tool(first)
        second_result = await agent.execute_tool(second)
        return agent, events, first_result, second_result

    agent, events, first_result, second_result = asyncio.run(scenario())
    assert agent.state == AgentState.FINISHED
    assert calls == ["mcp_revit_local_revit_run_ifc_assignment"]
    assert first_result.startswith("Error:")
    assert second_result.startswith("Error:")
    completed = [data for event, data in events if event == "tool_completed"]
    assert completed[0]["success"] is False
    last_assistant = [msg for msg in agent.memory.messages if msg.role == "assistant"][-1]
    assert "执行未成功" in last_assistant.content or "执行失败" in last_assistant.content



@pytest.mark.parametrize(
    "tool_name", ["mcp_revit_local_sz_ifc_open_model", "mcp_revit_local_revit_inspect_ifc"]
)
def test_sz_ifc_wait_can_be_cancelled_immediately(monkeypatch, tool_name):
    started = asyncio.Event()

    async def waiting_tool(self, command):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("app.api.runtime.Manus.execute_tool", waiting_tool)

    async def scenario():
        cancelled = asyncio.Event()
        events = []

        async def sink(event, data):
            events.append((event, data))

        agent = RuntimeManus(event_sink=sink, cancel_checker=cancelled.is_set)
        agent.tool_progress_interval_seconds = 0.001
        command = ToolCall(
            id="sz-open",
            function={"name": tool_name, "arguments": "{}"},
        )
        task = asyncio.create_task(agent.execute_tool(command))
        await started.wait()
        cancelled.set()
        result = await asyncio.wait_for(task, timeout=1)
        return result, events

    result, events = asyncio.run(scenario())
    assert '"status": "cancelled"' in result
    assert [data for event, data in events if event == "tool_completed"][-1]["success"] is False


def test_runtime_formats_sz_ifc_rule_selection():
    observation = (
        'Observed output of cmd `mcp_revit_local_revit_inspect_ifc` executed:\n'
        '{"status":"selection_required","message":"请选择规则",'
        '"candidates":[{"display_name":"建筑规则V1.2.0",'
        '"display_version":"SZ-IFC 规则","rule_name":"建筑规则V1.2.0"}]}'
    )
    question = RuntimeManus._input_required(
        "mcp_revit_local_revit_inspect_ifc", observation
    )

    assert "请选择规则" in question
    assert "建筑规则V1.2.0" in question


def test_runtime_turns_room_floor_selection_into_input_request():
    observation = (
        'Observed output of cmd `mcp_revit_local_revit_create_and_name_ar_rooms` executed:\n'
        '{"status":"selection_required","message":"请确认 DWG 对应楼层",'
        '"candidates":[{"dwg_path":"C:\\\\project\\\\六~三十层平面图.dwg",'
        '"floor_candidates":[6,30]}]}'
    )

    question = RuntimeManus._input_required(
        "mcp_revit_local_revit_create_and_name_ar_rooms", observation
    )

    assert "请确认 DWG 对应楼层" in question
    assert r"C:\project\六~三十层平面图.dwg" in question
    assert "6, 30" in question


def test_runtime_formats_complex_floor_candidates_friendly():
    observation = (
        'Observed output of cmd `mcp_revit_local_revit_create_and_name_ar_rooms` executed:\n'
        '{"status":"selection_required","message":"请确认 DWG 对应楼层",'
        '"candidates":[{"dwg_path":"D:\\\\教育基地\\\\图纸\\\\综合楼 五层平面图.dwg",'
        '"floor_candidates":['
        '{"matched_text":"三层工坊分体空调室外机","floor_entries":[3]},'
        '{"matched_text":"四层小展厅分体空调室外机","floor_entries":[4]},'
        '{"matched_text":"五层防火分区示意图","floor_entries":[5]}'
        ']}]}'
    )

    question = RuntimeManus._input_required(
        "mcp_revit_local_revit_create_and_name_ar_rooms", observation
    )

    assert "请确认 DWG 对应楼层" in question
    assert "综合楼 五层平面图.dwg" in question
    assert "3层（依据图纸标注：“三层工坊分体空调室外机”）" in question
    assert "4层（依据图纸标注：“四层小展厅分体空调室外机”）" in question
    assert "5层（依据图纸标注：“五层防火分区示意图”）" in question
    assert "matched_text" not in question
    assert "floor_entries" not in question


def test_runtime_manager_formats_rate_limit_and_budget_error_friendly():
    class FailingAgent:
        def __init__(self, sink, **_):
            self.sink = sink
            self.memory = Memory()

        def update_memory(self, *args):
            pass

        async def run(self, _):
            raise RuntimeError("RetryError[<Future at 0x123 state=finished raised RateLimitError>]")

    async def make_failing_agent(sink, *args, **kwargs):
        return FailingAgent(sink)

    manager = RuntimeManager(agent_factory=make_failing_agent)
    run = asyncio.run(manager.create_run(runtime_request()))

    async def get_events():
        events = []
        async for event in manager.stream_events(run.run_id):
            events.append(event)
        return events

    events = asyncio.run(get_events())
    failed_event = next(e for e in events if e.event == "run_failed")
    assert failed_event.data["error"] == "模型执行失败，请检查余额是否充足，如果不是请联系客服"


def test_format_completed_milestone_extracts_actual_model_path_from_payload():
    obs = json.dumps({
        "status": "ready",
        "model_path": r"C:\Folder\rvt\0513_js瑞府_地下室_AR-B3.rvt",
        "model_version": 2018,
    })
    milestone = RuntimeManus._format_completed_milestone(
        "revit_open_project_model",
        {"path": r"C:\Folder"},
        obs,
    )
    assert milestone is not None
    assert "0513_js瑞府_地下室_AR-B3.rvt" in milestone
    assert "2018" in milestone


def test_format_completed_milestone_extracts_room_and_export_details():
    room_obs = json.dumps({
        "status": "success",
        "room_count": 48,
        "named_room_count": 48,
        "saved_model_path": r"C:\result\model_rooms.rvt",
    })
    room_milestone = RuntimeManus._format_completed_milestone(
        "revit_create_and_name_ar_rooms",
        {},
        room_obs,
    )
    assert "48 个" in room_milestone
    assert "model_rooms.rvt" in room_milestone

    export_obs = json.dumps({
        "status": "success",
        "ifc_path": r"C:\result\model.ifc",
        "xlsx_path": r"C:\result\model.xlsx",
    })
    export_milestone = RuntimeManus._format_completed_milestone(
        "revit_export_ifc",
        {},
        export_obs,
    )
    assert "model.ifc" in export_milestone
    assert "model.xlsx" in export_milestone


def test_runtime_prepends_completed_milestones_to_user_action_required(monkeypatch):
    events = []

    async def sink(event, data):
        events.append((event, data))

    agent = RuntimeManus(event_sink=sink)
    agent.completed_milestones.append("Revit 建筑模型已成功打开：test.rvt")
    agent.completed_milestones.append("Revit 建筑房间批量创建与命名已完成（生成房间 10 个）")

    observation = json.dumps({
        "status": "user_action_required",
        "message": "请在 SZ-IFC 中手动加载目标模型",
        "ifc_path": r"C:\test.ifc",
    })

    async def fake_tool(self, command):
        return observation

    monkeypatch.setattr("app.api.runtime.Manus.execute_tool", fake_tool)

    cmd = ToolCall(
        id="sz-1",
        function={"name": "mcp_revit_local_revit_inspect_ifc", "arguments": "{}"},
    )
    asyncio.run(agent.execute_tool(cmd))

    input_req = next(data for ev, data in events if ev == "assistant_message")
    content = input_req["content"]
    assert "当前阶段已完成：" in content
    assert "test.rvt" in content
    assert "生成房间 10 个" in content
    assert "请在 SZ-IFC 中手动加载目标模型：C:\\test.ifc" in content


