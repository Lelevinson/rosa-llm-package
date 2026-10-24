import asyncio
import json

import pytest

pytest.importorskip("langchain_core", minversion="0.3.86")

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, ChatMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from rosa_llm import (
    ConfigurationError,
    IncompleteResponse,
    ProviderError,
    UnsupportedFeature,
)
from rosa_llm import Tool as RosaTool
from rosa_llm.langchain import HISTORY_KEY

from .conftest import completed


@tool
def echo(value: str) -> str:
    """Return value unchanged."""
    return value


def function_output(name="echo", args=None, call_id="call_1", namespace="local"):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "namespace": namespace,
        "arguments": json.dumps({"value": "Hello"} if args is None else args),
    }


def test_invoke_maps_messages_and_usage(model_factory):
    model, requests = model_factory()
    response = model.invoke(
        [
            SystemMessage(content="Be brief"),
            ChatMessage(role="developer", content="Use text"),
            HumanMessage(content=[{"type": "text", "text": "Hello"}]),
        ]
    )
    assert isinstance(response, AIMessage)
    assert response.content == "Hello"
    assert response.usage_metadata == {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}
    assert response.response_metadata["provider"] == "openai"
    assert response.response_metadata["model_name"] == "account-model"
    assert json.loads(requests[0].content)["input"] == [
        {"role": "developer", "content": "Be brief"},
        {"role": "developer", "content": "Use text"},
        {"role": "user", "content": "Hello"},
    ]


def test_tool_definitions_and_returned_arguments_are_preserved(model_factory):
    args = {"value": "", "extra": None}
    model, requests = model_factory([[completed(output=[function_output(args=args)])]])
    response = model.bind_tools([echo]).invoke("Echo")
    assert response.tool_calls == [
        {"name": "echo", "args": args, "id": "call_1", "type": "tool_call"}
    ]
    function = json.loads(requests[0].content)["tools"][0]["tools"][0]
    assert function["parameters"] == {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    # The adapter does not validate/coerce arguments on behalf of LangChain's executor.
    assert response.tool_calls[0]["args"]["extra"] is None


def test_callables_pydantic_and_schema_inputs(model_factory):
    def increment(value: int) -> int:
        """Increment value."""
        return value + 1

    class Lookup(BaseModel):
        """Lookup an identifier."""

        identifier: str

    model, _ = model_factory()
    bound = model.bind_tools(
        [
            increment,
            Lookup,
            {
                "type": "function",
                "name": "native",
                "description": "Native schema",
                "parameters": {"type": "object", "properties": {}},
            },
        ]
    )
    assert [t["function"]["name"] for t in bound.kwargs["tools"]] == [
        "increment",
        "Lookup",
        "native",
    ]


def test_binding_is_immutable_and_allowlist_is_enforced(model_factory):
    model, _ = model_factory(allowed_tools={"echo"})
    bound = model.with_config({"streaming": False}).bind_tools(
        [echo, RosaTool("other", "Not allowed")]
    )
    assert [t["function"]["name"] for t in bound.kwargs["tools"]] == ["echo"]
    assert model.allowed_tools == frozenset({"echo"})
    assert not hasattr(model, "tools")
    with pytest.raises(ConfigurationError):
        model.bind_tools([RosaTool("other", "Missing echo")])


@pytest.mark.parametrize("choice", ["required", "none", "echo"])
def test_forced_tool_choice_is_explicitly_unsupported(model_factory, choice):
    model, requests = model_factory(provider="ollama")
    with pytest.raises(UnsupportedFeature):
        model.bind_tools([echo], tool_choice=choice)
    assert requests == []


def test_strict_custom_and_hosted_tools_are_not_silently_changed(model_factory):
    model, _ = model_factory()
    bound = model.bind_tools([echo], strict=True)
    assert bound.kwargs["tools"][0]["function"]["strict"] is True
    with pytest.raises(UnsupportedFeature):
        model.bind_tools([RosaTool("custom", "Custom text", kind="custom")])
    with pytest.raises(UnsupportedFeature):
        model.bind_tools([{"type": "web_search_preview"}])


def test_history_round_trip_retains_reasoning_and_tool_id(model_factory):
    output = [
        {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
        function_output(),
    ]
    model, requests = model_factory([[completed(output=output)], [completed()]])
    bound = model.bind_tools([echo])
    first = bound.invoke("Echo Hello")
    second = bound.invoke(
        [
            HumanMessage(content="Echo Hello"),
            first,
            ToolMessage(content="Hello", tool_call_id="call_1", additional_kwargs={"name": "echo"}),
        ]
    )
    assert second.content == "Hello"
    body = json.loads(requests[1].content)
    assert body["input"][1:3] == output
    assert body["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Hello",
    }
    assert first.additional_kwargs[HISTORY_KEY]["items"] == output


def test_generic_assistant_and_named_tool_history(model_factory):
    model, requests = model_factory()
    model.bind_tools([echo]).invoke(
        [
            AIMessage(
                content="", tool_calls=[{"name": "echo", "args": {"value": "Hello"}, "id": "id"}]
            ),
            ToolMessage(content="Hello", tool_call_id="id"),
        ]
    )
    body = json.loads(requests[0].content)
    assert body["input"][0]["namespace"] == "local"
    assert body["input"][1]["call_id"] == "id"


def test_stream_does_not_duplicate_text_and_preserves_tool_chunks(model_factory):
    output = [*completed()["response"]["output"], function_output()]
    model, _ = model_factory(
        [
            [
                {"type": "response.output_text.delta", "delta": "Hel"},
                {"type": "response.output_text.delta", "delta": "lo"},
                completed(output=output),
            ]
        ]
    )
    chunks = list(model.bind_tools([echo]).stream("Echo Hello"))
    assert [c.content for c in chunks] == ["Hel", "lo", ""]
    assert all(not c.tool_calls for c in chunks[:-1])
    merged = chunks[0] + chunks[1:]
    assert merged.content == "Hello"
    assert merged.tool_calls[0]["args"] == {"value": "Hello"}
    assert merged.additional_kwargs[HISTORY_KEY]["items"] == output
    assert merged.usage_metadata["total_tokens"] == 5


def test_parallel_function_calls_keep_distinct_ids(model_factory):
    output = [function_output(call_id="a"), function_output(call_id="b")]
    model, _ = model_factory([[completed(output=output)]])
    chunks = list(model.bind_tools([echo]).stream("Echo twice"))
    assert [c["id"] for c in chunks[0].tool_calls] == ["a", "b"]


@pytest.mark.parametrize("method", ["invoke", "stream"])
def test_explicit_allowlist_rejects_unoffered_tool_request(model_factory, method):
    model, _ = model_factory(
        [[completed(output=[function_output(name="unoffered")])]], allowed_tools={"echo"}
    )
    bound = model.bind_tools([echo])
    with pytest.raises(ProviderError, match="allowlist"):
        result = getattr(bound, method)("hi")
        if method == "stream":
            list(result)


def test_incomplete_stream_releases_no_tool_request(model_factory):
    model, _ = model_factory(
        [[{"type": "response.output_item.done", "output_index": 0, "item": function_output()}]]
    )
    received = []
    with pytest.raises(IncompleteResponse):
        for chunk in model.bind_tools([echo]).stream("Echo"):
            received.extend(chunk.tool_calls)
    assert received == []


def test_streamed_final_text_mismatch_is_not_silently_corrected(model_factory):
    model, _ = model_factory(
        [[{"type": "response.output_text.delta", "delta": "wrong"}, completed()]]
    )
    with pytest.raises(ProviderError, match="does not match"):
        list(model.stream("hi"))


def test_async_invoke_and_stream_use_real_langchain_bridge(model_factory):
    model, _ = model_factory([[completed()], [completed()]])

    async def run():
        assert (await model.ainvoke("hi")).content == "Hello"
        chunks = [chunk async for chunk in model.astream("hi")]
        assert "".join(c.content for c in chunks) == "Hello"

    asyncio.run(run())


def test_async_events_and_callbacks_are_not_duplicated(model_factory):
    model, _ = model_factory(
        [[{"type": "response.output_text.delta", "delta": "Hello"}, completed()]]
    )

    async def run():
        events = [event async for event in model.astream_events("hi", version="v2")]
        assert events[0]["event"] == "on_chat_model_start"
        assert events[-1]["event"] == "on_chat_model_end"
        chunks = [
            event["data"]["chunk"] for event in events if event["event"] == "on_chat_model_stream"
        ]
        assert "".join(c.content for c in chunks) == "Hello"

    asyncio.run(run())

    class Tokens(BaseCallbackHandler):
        def __init__(self):
            self.tokens = []

        def on_llm_new_token(self, token, **kwargs):
            self.tokens.append(token)

    model, _ = model_factory(
        [[{"type": "response.output_text.delta", "delta": "Hello"}, completed()]]
    )
    callbacks = Tokens()
    list(model.stream("hi", config={"callbacks": [callbacks]}))
    assert "".join(callbacks.tokens) == "Hello"


def test_unsupported_stop_media_and_foreign_history_fail_before_network(model_factory):
    model, requests = model_factory()
    with pytest.raises(UnsupportedFeature):
        model.invoke("hi", stop=["END"])
    with pytest.raises(UnsupportedFeature):
        model.invoke(
            [HumanMessage(content=[{"type": "image_url", "image_url": "https://example.com/x"}])]
        )
    with pytest.raises(UnsupportedFeature):
        model.invoke(
            [
                AIMessage(
                    content="hi",
                    additional_kwargs={HISTORY_KEY: {"provider": "ollama", "items": []}},
                )
            ]
        )
    assert requests == []


def test_client_is_excluded_from_model_serialization(model_factory):
    model, _ = model_factory()
    assert "client" not in model.model_dump()
    assert "old-access" not in repr(model)
    assert model.cache is False


def test_ollama_function_results_keep_tool_name_and_native_thinking(model_factory):
    model, requests = model_factory(
        provider="ollama",
        responses=[
            [
                {
                    "message": {
                        "thinking": "reason",
                        "tool_calls": [
                            {"function": {"name": "echo", "arguments": {"value": "Hello"}}}
                        ],
                    },
                    "done": True,
                }
            ],
            [
                {
                    "message": {"content": "Hello"},
                    "done": True,
                    "prompt_eval_count": 2,
                    "eval_count": 1,
                }
            ],
        ],
    )
    bound = model.bind_tools([echo])
    first = bound.invoke("Echo")
    call = first.tool_calls[0]
    final = bound.invoke(
        [HumanMessage(content="Echo"), first, ToolMessage(content="Hello", tool_call_id=call["id"])]
    )
    body = json.loads(requests[-1].content)
    assert body["messages"][-2]["thinking"] == "reason"
    assert body["messages"][-1]["tool_name"] == "echo"
    assert final.usage_metadata == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
