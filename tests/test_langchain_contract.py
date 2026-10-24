"""General LangChain contracts; real tools, parsers and agents, mocked HTTP only."""
# ruff: noqa: E402

import asyncio
import json
from copy import deepcopy
from enum import Enum
from typing import Annotated

import httpx
import pytest

pytest.importorskip("langchain_core", minversion="0.3.86")

from langchain_core.caches import InMemoryCache
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import (
    BaseTool,
    InjectedToolCallId,
    StructuredTool,
    Tool,
    ToolException,
    tool,
)
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field
from pydantic.v1 import BaseModel as BaseModelV1
from typing_extensions import TypedDict

from rosa_llm import ConfigurationError, ProviderError, UnsupportedFeature
from rosa_llm.langchain import HISTORY_KEY, ChatRosaLLM

from .conftest import completed
from .test_langchain import echo, function_output


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


class Sample(BaseModel):
    label: str
    values: list[float]


class NestedInput(BaseModel):
    """Process nested samples without adapter coercion."""

    samples: list[Sample]
    color: Color
    enabled: bool
    note: str | None = None
    extra: dict[str, int | str] = Field(default_factory=dict)


class LegacyInput(BaseModelV1):
    """Read a legacy input."""

    value: int


class TypedInput(TypedDict):
    """Read a typed input."""

    value: str


class EmptyTool(BaseTool):
    name: str = "0-health-check"
    description: str = "Return health without arguments."

    def _run(self) -> str:
        return "healthy"


DEFINITIONS = [
    echo,
    EmptyTool(),
    NestedInput,
    LegacyInput,
    TypedInput,
    {"name": "bare", "parameters": {"type": "object", "properties": {}}},
    {"title": "schema", "type": "object", "properties": {"value": {"type": "integer"}}},
    {"name": "anthropic", "input_schema": {"type": "object", "properties": {}}},
    {"toolSpec": {"name": "bedrock", "inputSchema": {"json": {"type": "object"}}}},
    {"type": "function", "name": "flat", "parameters": {"type": "object"}},
    {"type": "function", "function": {"name": "wrapped", "parameters": {"type": "object"}}},
    Tool(name="legacy_string", func=lambda value: value, description="Echo a string."),
]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize(
    "definition", DEFINITIONS, ids=lambda t: getattr(t, "__name__", str(type(t)))
)
def test_standard_schema_conversion_is_preserved(model_factory, provider, definition):
    model, _ = model_factory(provider=provider)
    reference = convert_to_openai_tool(definition)
    expected = reference.get("function", reference)
    bound = model.bind_tools([definition])
    actual = bound.kwargs["tools"][0]["function"]
    assert actual["name"] == expected["name"]
    assert actual["parameters"] == expected.get("parameters", {"type": "object", "properties": {}})
    assert actual["description"] == expected.get("description", "")
    json.dumps(bound.kwargs)  # Bindings contain standard serializable tool dictionaries.


def executor_for(model, tools, **kwargs):
    agents = pytest.importorskip("langchain.agents")
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Use tools when needed."),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    return agents.AgentExecutor(
        agent=agents.create_tool_calling_agent(model, tools, prompt),
        tools=tools,
        max_iterations=4,
        return_intermediate_steps=True,
        **kwargs,
    )


def provider_events(provider, calls=None, text="finished"):
    if provider == "openai":
        return [completed(output=calls)] if calls is not None else [completed(text)]
    message = {"content": text if calls is None else ""}
    if calls is not None:
        message["tool_calls"] = [
            {
                "function": {
                    "name": c["name"],
                    "arguments": c["arguments"],
                }
            }
            for c in calls
        ]
    return [{"message": message, "done": True}]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_agent_executes_nested_empty_and_legacy_string_tools(
    model_factory, provider, asynchronous
):
    received = []

    @tool(args_schema=NestedInput)
    def process(samples, color, enabled, note=None, extra=None):
        """Process samples."""
        received.append((samples, color, enabled, note, extra))
        return {"count": len(samples), "label": samples[0].label}

    legacy = Tool(name="legacy_string", func=lambda value: value.upper(), description="Uppercase.")
    args = {
        "samples": [{"label": "台灣", "values": [0, -1.25]}],
        "color": "blue",
        "enabled": False,
        "note": None,
        "extra": {"zero": 0, "text": "[1,2]"},
    }
    calls = [
        function_output(name="process", args=args, call_id="nested"),
        function_output(name="0-health-check", args={}, call_id="empty"),
        function_output(name="legacy_string", args={"__arg1": "hello"}, call_id="string"),
    ]
    model, requests = model_factory(
        provider=provider,
        responses=[
            provider_events(provider, calls),
            provider_events(provider),
        ],
    )
    executor = executor_for(model, [process, EmptyTool(), legacy])
    result = (
        asyncio.run(executor.ainvoke({"input": "Process"}))
        if asynchronous
        else executor.invoke({"input": "Process"})
    )
    assert result["output"] == "finished"
    assert received[0][0][0] == Sample(label="台灣", values=[0, -1.25])
    assert received[0][1:] == (Color.BLUE, False, None, {"zero": 0, "text": "[1,2]"})
    assert [observation for _, observation in result["intermediate_steps"]] == [
        {"count": 1, "label": "台灣"},
        "healthy",
        "HELLO",
    ]
    # Only LangChain unwraps __arg1 and validates nested Pydantic values.
    assert result["intermediate_steps"][0][0].tool_input == args
    body = json.loads(requests[-1].content)
    if provider == "openai":
        outputs = [m for m in body["input"] if m.get("type") == "function_call_output"]
        assert [m["call_id"] for m in outputs] == ["nested", "empty", "string"]
    else:
        outputs = [m for m in body["messages"] if m["role"] == "tool"]
        assert [m["tool_name"] for m in outputs] == ["process", "0-health-check", "legacy_string"]


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_direct_tool_invocation_injected_id_artifacts_and_reversed_results(model_factory, provider):
    @tool(response_format="content_and_artifact")
    def remember(value: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> tuple:
        """Remember a value with its call ID."""
        return value, {"id": tool_call_id, "secret_artifact": "local-only"}

    calls = [
        function_output(name="remember", args={"value": value}, call_id=value)
        for value in ["a", "b"]
    ]
    model, requests = model_factory(
        provider=provider,
        responses=[
            provider_events(provider, calls),
            provider_events(provider),
        ],
    )
    bound = model.bind_tools([remember])
    assert "tool_call_id" not in bound.kwargs["tools"][0]["function"]["parameters"]["properties"]
    first = bound.invoke("Remember")
    results = [remember.invoke(c) for c in reversed(first.tool_calls)]
    assert all(isinstance(r, ToolMessage) and r.artifact["id"] == r.tool_call_id for r in results)
    bound.invoke([HumanMessage(content="Remember"), first, *results])
    assert "secret_artifact" not in requests[-1].content.decode()


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_unknown_function_is_left_to_standard_agent_invalid_tool_handling(model_factory, provider):
    model, _ = model_factory(
        provider=provider,
        responses=[
            provider_events(provider, [function_output(name="missing")]),
            provider_events(provider),
        ],
    )
    result = executor_for(model, [echo]).invoke({"input": "Echo"})
    assert result["output"] == "finished"
    assert "not a valid tool" in result["intermediate_steps"][0][1]


def test_coroutine_tool_runs_through_async_agent(model_factory):
    seen = []

    async def reverse(value: str) -> str:
        """Reverse value."""
        seen.append(value)
        return value[::-1]

    custom = StructuredTool.from_function(coroutine=reverse)
    model, _ = model_factory(
        responses=[
            [completed(output=[function_output(name="reverse", args={"value": "abc"})])],
            [completed("cba")],
        ]
    )
    result = asyncio.run(executor_for(model, [custom]).ainvoke({"input": "Reverse abc"}))
    assert seen == ["abc"] and result["output"] == "cba"


def test_tool_error_status_is_sent_as_content_and_return_direct_stops_loop(model_factory):
    @tool(return_direct=True)
    def fail(value: str) -> str:
        """Demonstrate a handled tool failure."""
        raise ToolException("unavailable")

    fail.handle_tool_error = True
    model, requests = model_factory(
        [
            [completed(output=[function_output(name="fail")])],
            [completed("Failure observed")],
        ]
    )
    result = executor_for(model, [fail]).invoke({"input": "Try"})
    assert result["output"] == "unavailable" and len(requests) == 1
    tool_result = fail.invoke(
        {"name": "fail", "args": {"value": "x"}, "id": "call_1", "type": "tool_call"}
    )
    assert tool_result.status == "error"
    model.invoke([result["intermediate_steps"][0][0].message_log[0], tool_result])
    assert json.loads(requests[-1].content)["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "unavailable",
    }


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_serialized_history_replays_but_edits_override_saved_wire_items(model_factory, provider):
    model, requests = model_factory(
        provider=provider,
        responses=[
            provider_events(provider, [function_output()]),
            provider_events(provider),
        ],
    )
    bound = model.bind_tools([echo])
    original = bound.invoke("Echo")
    restored = messages_from_dict(messages_to_dict([original]))[0]
    restored.tool_calls[0]["args"]["value"] = "edited"
    restored.content = "New text"
    result = echo.invoke(restored.tool_calls[0])
    bound.invoke([HumanMessage(content="Echo"), restored, result])
    body = json.loads(requests[-1].content)
    if provider == "openai":
        sent = next(m for m in body["input"] if m.get("type") == "function_call")
        assert json.loads(sent["arguments"]) == {"value": "edited"}
    else:
        assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"value": "edited"}
    assert original.tool_calls[0]["args"] == {"value": "Hello"}


def test_history_partition_and_legacy_state_rejected(model_factory):
    model, _ = model_factory()
    first = model.invoke("Hi")
    other = ChatRosaLLM(client=model.client)
    assert other._history_scope == model._history_scope
    assert other._identifying_params != model._identifying_params
    changed = deepcopy(first)
    changed.additional_kwargs[HISTORY_KEY]["scope"] = "different-account-location"
    with pytest.raises(UnsupportedFeature, match="history"):
        model.invoke([changed])
    changed = deepcopy(first)
    del changed.additional_kwargs[HISTORY_KEY]["version"]
    with pytest.raises(UnsupportedFeature):
        model.invoke([changed])


def test_cache_does_not_cross_client_instances(model_factory):
    cache = InMemoryCache()
    model, requests = model_factory()
    model.cache = cache
    assert model.invoke("Hi").content == "Hello"
    assert model.invoke("Hi").content == "Hello" and len(requests) == 1
    other, other_requests = model_factory([[completed("different")]])
    other.cache = cache
    assert other.invoke("Hi").content == "different" and len(other_requests) == 1


def test_ollama_generated_call_ids_are_unique_across_turns(model_factory):
    model, _ = model_factory(
        provider="ollama",
        responses=[
            provider_events("ollama", [function_output()]),
            provider_events("ollama", [function_output()]),
        ],
    )
    bound = model.bind_tools([echo])
    first = bound.invoke("One")
    second = bound.invoke([HumanMessage(content="One"), first, echo.invoke(first.tool_calls[0])])
    assert first.tool_calls[0]["id"] != second.tool_calls[0]["id"]


def test_ollama_dictionary_arguments_are_not_subject_to_upstream_repair_rules(model_factory):
    arguments = {
        "value": '[{"quoted":true}]',
        "functionName": "echo",
        "zero": 0,
        "empty": "",
        "null": None,
        "boolean": False,
    }
    call = function_output(args=arguments)
    call["arguments"] = arguments  # Native object envelope, not the JSON-string variant.
    model, _ = model_factory(provider="ollama", responses=[provider_events("ollama", [call])])
    assert model.bind_tools([echo]).invoke("Echo").tool_calls[0]["args"] == arguments


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_completed_stream_aggregates_into_a_replayable_tool_message(model_factory, provider):
    model, requests = model_factory(
        provider=provider,
        responses=[
            provider_events(provider, [function_output()]),
            provider_events(provider),
        ],
    )
    bound = model.bind_tools([echo])

    async def run():
        chunks = [chunk async for chunk in bound.astream("Echo")]
        aggregate = chunks[0]
        for chunk in chunks[1:]:
            aggregate += chunk
        assert aggregate.tool_calls[0]["args"] == {"value": "Hello"}
        result = echo.invoke(aggregate.tool_calls[0])
        return await bound.ainvoke([HumanMessage(content="Echo"), aggregate, result])

    final = asyncio.run(run())
    assert final.content == "finished" and len(requests) == 2


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("auto", "auto"),
        ("none", "none"),
        ("required", "required"),
        ("any", "required"),
        (True, "required"),
        (False, None),
    ],
)
def test_openai_choice_and_parallel_controls(model_factory, choice, expected):
    model, requests = model_factory()
    model.bind_tools([echo], tool_choice=choice, parallel_tool_calls=False).invoke("Hi")
    body = json.loads(requests[0].content)
    assert body.get("tool_choice") == expected
    assert body["parallel_tool_calls"] is False


def test_strict_uses_langchain_conversion_without_mutating_schema(model_factory):
    model, requests = model_factory()
    schema = convert_to_openai_tool(NestedInput)
    before = deepcopy(schema)
    model.bind_tools([schema], strict=True).invoke("Hi")
    function = json.loads(requests[0].content)["tools"][0]["tools"][0]
    assert function["strict"] is True
    assert function["parameters"]["additionalProperties"] is False
    assert schema == before
    with pytest.raises(ValueError):
        model.bind_tools([{"name": "conflict", "strict": True}], strict=False)


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("choice", ["echo", {"type": "function", "function": {"name": "echo"}}])
def test_named_choice_rejected_before_network(model_factory, provider, choice):
    # Live OAuth rejected both bare and qualified names with the namespace envelope.
    model, requests = model_factory(provider=provider)
    with pytest.raises(UnsupportedFeature):
        model.bind_tools([echo], tool_choice=choice)
    assert not requests


@pytest.mark.parametrize(
    "definition",
    [
        {"name": "unsupported", "defer_loading": True},
        {"type": "function", "name": "unsupported", "defer_loading": True},
        {"type": "function", "function": {"name": "unsupported", "defer_loading": True}},
        {"type": "function", "function": {"name": "unsupported"}, "cache_control": {}},
    ],
)
def test_unsupported_function_fields_are_not_lost_in_conversion(model_factory, definition):
    model, requests = model_factory()
    with pytest.raises(UnsupportedFeature):
        model.bind_tools([definition])
    assert not requests


def test_none_tool_definition_is_rejected_by_standard_converter(model_factory):
    model, _ = model_factory()
    with pytest.raises(ValueError):
        model.bind_tools([None])


def test_default_structured_output_reuses_langchain_parser(model_factory):
    class Answer(BaseModel):
        """Return a number."""

        number: int

    model, requests = model_factory(
        [[completed(output=[function_output(name="Answer", args={"number": 3})])]]
    )
    result = model.with_structured_output(Answer, include_raw=True).invoke("Three")
    assert result["parsed"] == Answer(number=3) and result["parsing_error"] is None
    assert json.loads(requests[0].content)["tool_choice"] == "required"
    with pytest.raises(UnsupportedFeature):
        model.with_structured_output(Answer, method="json_schema")
    with pytest.raises(UnsupportedFeature):
        model.with_structured_output(Answer, strict=True)


def test_ollama_stop_and_generation_options_are_forwarded_once(model_factory):
    model, requests = model_factory(provider="ollama", responses=[provider_events("ollama")])
    model.bind_tools([echo], options={"temperature": 0}, think=False).invoke("Hi", stop=["END"])
    body = json.loads(requests[0].content)
    assert body["options"] == {"temperature": 0, "stop": ["END"]} and body["think"] is False
    with pytest.raises(ConfigurationError):
        model.invoke("Hi", stop=["END"], options={"stop": ["OTHER"]})


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_duplicate_ids_and_conflicting_result_names_fail_before_network(model_factory, provider):
    model, requests = model_factory(provider=provider)
    assistant = AIMessage(content="", tool_calls=[{"name": "echo", "args": {}, "id": "a"}])
    with pytest.raises(ConfigurationError, match="unique"):
        model.invoke([assistant, assistant])
    with pytest.raises(ConfigurationError, match="conflicts"):
        model.invoke([assistant, ToolMessage(content="x", tool_call_id="a", name="other")])
    assert not requests


@pytest.mark.parametrize("call_id", [None, "", 123])
def test_openai_invalid_call_ids_fail_as_provider_errors(model_factory, call_id):
    model, _ = model_factory([[completed(output=[function_output(call_id=call_id)])]])
    with pytest.raises(ProviderError, match="IDs"):
        model.bind_tools([echo]).invoke("Echo")


def test_openai_nameless_tool_result_needs_only_call_id(model_factory):
    model, requests = model_factory()
    model.invoke([ToolMessage(content="already complete", tool_call_id="external")])
    assert json.loads(requests[0].content)["input"][0]["call_id"] == "external"


@pytest.mark.parametrize("provider", ["openai", "ollama"])
@pytest.mark.parametrize("raw", ["null", "[]", "false", '"text"', '{"unfinished":'])
def test_invalid_function_arguments_are_never_repaired_or_executed(model_factory, provider, raw):
    call = function_output()
    call["arguments"] = raw
    model, _ = model_factory(provider=provider, responses=[provider_events(provider, [call])])
    with pytest.raises(ProviderError):
        executor_for(model, [echo]).invoke({"input": "Echo"})


def test_usage_token_details_are_standardized(model_factory):
    event = completed()
    event["response"]["usage"].update(
        {
            "input_tokens_details": {"cached_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 1},
        }
    )
    model, _ = model_factory([[event]])
    usage = model.invoke("Hi").usage_metadata
    assert usage["input_token_details"] == {"cache_read": 2}
    assert usage["output_token_details"] == {"reasoning": 1}


def test_batch_async_batch_and_prompt_pipeline(model_factory):
    model, _ = model_factory([[completed()] for _ in range(6)])
    chain = ChatPromptTemplate.from_template("Say {value}") | model
    assert [m.content for m in chain.batch([{"value": "a"}, {"value": "b"}])] == ["Hello"] * 2
    result = asyncio.run(chain.abatch([{"value": "c"}, {"value": "d"}]))
    assert [m.content for m in result] == ["Hello"] * 2


def test_closing_stream_releases_http_response(model_factory):
    class TrackingStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
            pytest.fail("A closed stream must not read another event")

        def close(self):
            self.closed = True

    wire = TrackingStream()
    model, _ = model_factory(handler=lambda _: httpx.Response(200, stream=wire))
    stream = model.stream("Hi")
    assert next(stream).content == "Hi"
    stream.close()
    assert wire.closed


def test_closing_async_stream_releases_http_response(model_factory):
    class TrackingStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
            pytest.fail("A closed stream must not read another event")

        def close(self):
            self.closed = True

    wire = TrackingStream()
    model, _ = model_factory(handler=lambda _: httpx.Response(200, stream=wire))

    async def run():
        stream = model.astream("Hi")
        assert (await anext(stream)).content == "Hi"
        await stream.aclose()
        # Closing the public iterator should release the transport within the loop.
        await asyncio.sleep(0)
        assert wire.closed

    asyncio.run(run())


def test_explicit_allowlist_does_not_disable_plain_chat(model_factory):
    model, _ = model_factory(allowed_tools={"echo"})
    assert model.invoke("Hi").content == "Hello"


def test_binding_matches_established_chatopenai_tool_schema(model_factory):
    integration = pytest.importorskip("langchain_openai")
    model, _ = model_factory()
    reference = integration.ChatOpenAI(api_key="comparison-only", model="unused")
    assert (
        model.bind_tools([NestedInput, echo]).kwargs["tools"]
        == reference.bind_tools([NestedInput, echo]).kwargs["tools"]
    )
