import json

import httpx
import pytest

from rosa_llm import (
    AuthenticationError,
    Config,
    ConfigurationError,
    IncompleteResponse,
    Message,
    PermissionDenied,
    ProviderError,
    Tool,
    UnsupportedFeature,
    UsageLimitError,
    create_client,
)
from rosa_llm.providers import sse_events

from .conftest import completed, sse


def test_openai_stream_and_unified_response(openai_client):
    client, seen = openai_client(
        [{"type": "response.output_text.delta", "delta": "Hello"}, completed()]
    )
    events = list(client.stream("Say hello"))
    assert events[0].text == "Hello"
    response = events[-1].response
    assert response.provider == "openai" and response.text == "Hello"
    assert response.model == "account-model"
    assert response.usage["input_tokens"] == 4
    assert response.to_dict()["response"] == "Hello"
    assert "access_token" not in json.dumps(response.to_dict())
    payload = json.loads(seen[-1].content)
    assert payload == {
        "model": "account-model",
        "input": [{"role": "user", "content": "Say hello"}],
        "store": False,
        "stream": True,
    }
    assert str(seen[-1].url) == "https://api.openai.com/v1/responses"


def test_account_catalog_order_model_override_and_missing_model(openai_client):
    client, _ = openai_client(model="second-model")
    assert client.generate("hi").text == "Hello"
    client, seen = openai_client(model="missing-model")
    with pytest.raises(ConfigurationError):
        client.generate("hi")
    assert len(seen) == 1
    client, _ = openai_client(catalog=[])
    with pytest.raises(ProviderError, match="no visible models"):
        client.generate("hi")


@pytest.mark.parametrize(
    "events,exception",
    [
        ([{"type": "response.output_text.delta", "delta": "partial"}], IncompleteResponse),
        ([{"type": "response.incomplete", "response": {}}], IncompleteResponse),
        (
            [
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
                }
            ],
            UsageLimitError,
        ),
        (
            [
                {"type": "response.output_text.delta", "delta": "partial"},
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "subscription_sharing_usage_unavailable"}},
                },
            ],
            ProviderError,
        ),
        ([{"type": "error", "code": "unknown_error"}], ProviderError),
    ],
)
def test_terminal_events_and_limits(openai_client, events, exception):
    client, _ = openai_client(events)
    with pytest.raises(exception):
        client.generate("hi")


@pytest.mark.parametrize(
    "status,exception",
    [
        (401, AuthenticationError),
        (403, PermissionDenied),
        (429, UsageLimitError),
        (500, ProviderError),
    ],
)
def test_http_errors_are_safe(openai_client, status, exception):
    client, _ = openai_client(
        handler=lambda request: httpx.Response(
            status, json={"error": {"code": "anything", "message": "secret-token-callback-params"}}
        )
    )
    with pytest.raises(exception) as error:
        client.generate("hi")
    assert "secret-token" not in str(error.value)


def test_incomplete_status_on_completion_event(openai_client):
    event = completed()
    event["response"]["status"] = "incomplete"
    client, _ = openai_client([event])
    with pytest.raises(IncompleteResponse):
        client.generate("hi")


def test_function_round_trip_preserves_reasoning_and_namespace(openai_client):
    output = [
        {"type": "reasoning", "id": "reason_1", "encrypted_content": "opaque", "summary": []},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "echo",
            "namespace": "local",
            "arguments": '{"value":"Hello"}',
        },
    ]
    responses = [completed(output=output), completed()]

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse(responses.pop(0))
        )

    client, seen = openai_client(handler=handler)
    tools = [
        Tool(
            "echo", "Echo a value", {"type": "object", "properties": {"value": {"type": "string"}}}
        )
    ]
    history = [Message("system", "Use the echo tool"), Message("user", "Echo Hello")]
    first = client.generate(history, tools=tools)
    assert first.tool_calls[0].arguments == {"value": "Hello"}
    assert first.tool_calls[0].namespace == "local"
    call = first.tool_calls[0]
    history += [
        first.message,
        Message("tool", call.arguments["value"], tool_call_id=call.id, tool_name="echo"),
    ]
    final = client.generate(history, tools=tools)
    assert final.text == "Hello" and not final.tool_calls
    body = json.loads(seen[-1].content)
    assert body["input"][0]["role"] == "developer"
    assert body["input"][2:4] == output
    assert body["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Hello",
    }
    assert body["tools"][0]["type"] == "namespace"
    assert body["tools"][0]["tools"][0]["type"] == "function"
    assert "previous_response_id" not in body


def test_custom_tool_round_trip(openai_client):
    output = [
        {
            "type": "custom_tool_call",
            "call_id": "custom_1",
            "name": "echo",
            "namespace": "local",
            "input": "hello",
        }
    ]
    client, seen = openai_client([completed(output=output)])
    tool = Tool("echo", "Echo text", kind="custom", format={"type": "text"})
    first = client.generate("echo hello", tools=[tool])
    assert first.tool_calls[0].arguments == "hello"
    assert first.tool_calls[0].kind == "custom"
    list(
        client.stream(
            [first.message, Message("tool", "hello", tool_call_id="custom_1", tool_kind="custom")],
            tools=[tool],
        )
    )
    body = json.loads(seen[-1].content)
    assert body["input"][-1]["type"] == "custom_tool_call_output"
    assert body["tools"][0]["tools"][0]["format"] == {"type": "text"}


def test_unsupported_openai_options_fail_before_network(openai_client):
    client, seen = openai_client()
    with pytest.raises(UnsupportedFeature):
        client.generate("hi", temperature=0)
    assert seen == []


def test_foreign_history_fails_explicitly(openai_client):
    client, seen = openai_client()
    with pytest.raises(UnsupportedFeature):
        client.generate(
            [
                Message(
                    "assistant",
                    provider="ollama",
                    provider_items=[{"role": "assistant", "content": "hi"}],
                )
            ]
        )
    assert seen == []


@pytest.mark.parametrize("content", [b"data: {invalid}\n\n", b"<html>wrong</html>"])
def test_malformed_streams(openai_client, content):
    client, _ = openai_client(
        handler=lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=content
        )
    )
    with pytest.raises(ProviderError):
        client.generate("hi")


def test_sse_comments_multiline_and_final_unterminated_event():
    events = list(
        sse_events(
            iter(
                [
                    ": heartbeat",
                    "event: message",
                    'data: {"type":',
                    'data: "test"}',
                    "",
                    'data: {"type":"end"}',
                ]
            )
        )
    )
    assert events == [{"type": "test"}, {"type": "end"}]


def ollama_client(chunks, seen, status=200):
    def dispatch(request):
        seen.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model"}]})
        return httpx.Response(
            status,
            content="\n".join(json.dumps(c) for c in chunks),
            headers={"content-type": "application/x-ndjson"},
        )

    return create_client(
        Config(ollama_model="test-model"),
        http=httpx.Client(transport=httpx.MockTransport(dispatch)),
    )


def test_ollama_stream_history_usage_and_models():
    seen = []
    client = ollama_client(
        [
            {"message": {"thinking": "private thinking", "content": "He"}, "done": False},
            {
                "message": {"content": "llo"},
                "done": True,
                "model": "test-model",
                "prompt_eval_count": 3,
                "eval_count": 1,
                "total_duration": 20,
            },
        ],
        seen,
    )
    response = client.generate([Message("user", "hi")], think=False)
    assert response.provider == "ollama" and response.text == "Hello"
    assert response.usage == {"prompt_eval_count": 3, "eval_count": 1}
    assert response.message.provider_items[0]["thinking"] == "private thinking"
    assert client.models()[0]["id"] == "test-model"
    assert "Authorization" not in seen[0].headers
    assert json.loads(seen[0].content)["think"] is False


def test_ollama_function_round_trip():
    seen = []
    client = ollama_client(
        [
            {
                "done": True,
                "message": {
                    "tool_calls": [{"function": {"name": "echo", "arguments": {"value": "hello"}}}]
                },
            }
        ],
        seen,
    )
    tools = [Tool("echo", "Echo")]
    first = client.generate("hello", tools=tools)
    call = first.tool_calls[0]
    client.generate(
        [
            Message("user", "hello"),
            first.message,
            Message("tool", "hello", tool_call_id=call.id, tool_name=call.name),
        ],
        tools=tools,
    )
    body = json.loads(seen[-1].content)
    assert body["messages"][-1] == {"role": "tool", "content": "hello", "tool_name": "echo"}
    assert body["tools"][0]["function"]["name"] == "echo"


@pytest.mark.parametrize(
    "chunks",
    [
        [{"done": False, "message": {"content": "partial"}}],
        [{"done": True, "done_reason": "length", "message": {"content": "partial"}}],
    ],
)
def test_ollama_incomplete(chunks):
    with pytest.raises(IncompleteResponse):
        ollama_client(chunks, []).generate("hi")


def test_ollama_does_not_fall_back_and_rejects_custom_tools():
    client = ollama_client([{"error": "secret server body"}], [])
    with pytest.raises(ProviderError, match="Ollama reported"):
        client.generate("hi")
    with pytest.raises(UnsupportedFeature):
        client.generate("hi", tools=[Tool("echo", "Echo", kind="custom")])


def test_malformed_tool_arguments_fail(openai_client):
    client, _ = openai_client(
        [
            completed(
                output=[
                    {
                        "type": "function_call",
                        "name": "echo",
                        "call_id": "id",
                        "arguments": "not-json",
                    }
                ]
            )
        ]
    )
    with pytest.raises(ProviderError, match="malformed"):
        client.generate("hi")


def test_connection_errors_are_sanitized():
    def dispatch(request):
        raise httpx.ConnectError("secret request url", request=request)

    client = create_client(http=httpx.Client(transport=httpx.MockTransport(dispatch)))
    with pytest.raises(ProviderError) as error:
        client.generate("hi")
    assert "secret" not in str(error.value)


def test_empty_completion_is_not_a_useful_response(openai_client):
    client, _ = openai_client([completed(text="")])
    with pytest.raises(IncompleteResponse, match="without text"):
        client.generate("hi")


@pytest.mark.parametrize(
    "events,expected_error",
    [
        ([completed(text="ROSA_LLM_OK")], None),
        ([{"type": "response.output_text.delta", "delta": "partial"}], IncompleteResponse),
        (
            [
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
                }
            ],
            UsageLimitError,
        ),
    ],
)
def test_sse_without_content_type_requires_valid_completion(openai_client, events, expected_error):
    client, _ = openai_client(handler=lambda request: httpx.Response(200, content=sse(*events)))
    if expected_error:
        with pytest.raises(expected_error):
            client.generate("hi")
    else:
        assert client.generate("hi").text == "ROSA_LLM_OK"


@pytest.mark.parametrize("content_type", ["application/json", "text/html"])
def test_explicit_non_sse_content_type_is_rejected(openai_client, content_type):
    client, _ = openai_client(
        handler=lambda request: httpx.Response(
            200, headers={"content-type": content_type}, content=sse(completed())
        )
    )
    with pytest.raises(ProviderError, match="content type"):
        client.generate("hi")


@pytest.mark.parametrize(
    "output",
    [
        completed()["response"]["output"],
        [
            {"type": "reasoning", "id": "reason_1", "summary": [], "encrypted_content": "opaque"},
            {
                "type": "function_call",
                "call_id": "fn_1",
                "name": "echo",
                "namespace": "local",
                "arguments": '{"value":"Hello"}',
            },
        ],
        [
            {
                "type": "custom_tool_call",
                "call_id": "custom_1",
                "name": "echo",
                "namespace": "local",
                "input": "Hello",
            }
        ],
    ],
)
def test_done_items_preserved_when_terminal_output_is_empty(openai_client, output):
    events = [
        {"type": "response.output_item.done", "output_index": index, "item": item}
        for index, item in enumerate(output)
    ]
    events.append(completed(output=[]))
    client, _ = openai_client(handler=lambda request: httpx.Response(200, content=sse(*events)))
    response = client.generate("hi")
    assert response.message.provider_items == output
    if output[0]["type"] == "message":
        assert response.text == "Hello"
    else:
        assert response.tool_calls[0].name == "echo"


@pytest.mark.parametrize(
    "code,exception",
    [
        ("subscription_sharing_user_not_eligible", PermissionDenied),
        ("subscription_sharing_unsupported_capability", UnsupportedFeature),
        ("chatpass_v2_scope_not_authorized", PermissionDenied),
        ("subscription_sharing_invalid_user", AuthenticationError),
    ],
)
def test_structured_stream_errors_explain_restrictions(openai_client, code, exception):
    client, _ = openai_client([{"type": "response.failed", "response": {"error": {"code": code}}}])
    with pytest.raises(exception):
        client.generate("hi")
