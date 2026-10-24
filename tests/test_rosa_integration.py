"""Actual ROSA/LangChain execution with mocked HTTP and a real harmless Python tool."""
# ruff: noqa: E402

import asyncio
import json

import pytest

pytest.importorskip("langchain")
rosa_module = pytest.importorskip("rosa.rosa")
pytest.importorskip("rclpy")

from langchain_core.tools import tool
from langchain_core.tracers.context import collect_runs

from .conftest import completed
from .test_langchain import function_output


def test_unmodified_rosa_invoke_executes_tool_and_retains_turn_history(model_factory):
    calls = []

    @tool
    def echo(value: str) -> str:
        """Return value unchanged."""
        calls.append(value)
        return value

    model, requests = model_factory(
        allowed_tools={"echo"}, responses=[[completed(output=[function_output()])], [completed()]]
    )
    agent = rosa_module.ROSA(
        ros_version=2, llm=model, tools=[echo], streaming=False, verbose=False, max_iterations=3
    )
    with collect_runs() as collector:
        result = agent.invoke("Echo Hello")
    assert result == "Hello" and calls == ["Hello"]
    pending = list(collector.traced_runs)
    tool_runs = []
    while pending:
        run = pending.pop()
        pending.extend(run.child_runs)
        if run.run_type == "tool":
            tool_runs.append(run)
    assert [run.name for run in tool_runs] == ["echo"]
    # LangChain's local collector retains the tool input as its original display string.
    assert tool_runs[0].inputs == {"input": "{'value': 'Hello'}"}
    assert tool_runs[0].outputs == {"output": "Hello"}
    assert tool_runs[0].error is None
    assert len(agent.chat_history) == 2
    assert agent.chat_history[-1].content == "Hello"
    body = json.loads(requests[-1].content)
    assert [t["name"] for ns in body["tools"] for t in ns["tools"]] == ["echo"]
    assert body["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Hello",
    }


def test_unmodified_rosa_async_stream_reports_actual_tool_events(model_factory):
    calls = []

    @tool
    def echo(value: str) -> str:
        """Return value unchanged."""
        calls.append(value)
        return value

    model, _ = model_factory(
        allowed_tools={"echo"}, responses=[[completed(output=[function_output()])], [completed()]]
    )
    agent = rosa_module.ROSA(
        ros_version=2, llm=model, tools=[echo], streaming=True, verbose=False, max_iterations=3
    )

    async def run():
        return [event async for event in agent.astream("Echo Hello")]

    events = asyncio.run(run())
    assert not [e for e in events if e["type"] == "error"]
    assert calls == ["Hello"]
    assert [e["name"] for e in events if e["type"] == "tool_start"] == ["echo"]
    assert [e["output"] for e in events if e["type"] == "tool_end"] == ["Hello"]
    assert [e["content"] for e in events if e["type"] == "final"] == ["Hello"]
