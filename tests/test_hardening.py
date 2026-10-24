"""Regression evidence for transport, history and research provenance defects."""

import json
from copy import deepcopy

import httpx
import pytest

from rosa_llm import Config, Message, ProviderError, Tool, UnsupportedFeature, create_client
from rosa_llm.http import check_response

from .conftest import completed
from .test_providers import ollama_client


def test_standalone_openai_history_edits_are_authoritative(openai_client):
    client, requests = openai_client()
    response = client.generate("Hello")
    response.message.content = "edited"
    client.generate([response.message, Message("user", "Continue")])
    body = json.loads(requests[-1].content)
    assert body["input"][0]["content"][0]["text"] == "edited"


def test_standalone_ollama_history_edits_are_authoritative():
    requests = []
    client = ollama_client([{"message": {"content": "original"}, "done": True}], requests)
    first = client.generate("Hi")
    first.message.content = "edited"
    client.generate([first.message, Message("user", "Continue")])
    assert json.loads(requests[-1].content)["messages"][0]["content"] == "edited"
    client.http.close()


@pytest.mark.parametrize("status", [400, 500])
def test_http_errors_keep_safe_machine_readable_details(status):
    response = httpx.Response(
        status,
        headers={"x-request-id": "req_probe"},
        json={
            "error": {"code": "example_failure", "message": "private-server-message"},
        },
    )
    with pytest.raises(ProviderError) as caught:
        check_response(response)
    error = caught.value
    assert error.status_code == status
    assert error.provider_code == "example_failure"
    assert error.request_id == "req_probe"
    assert "private-server-message" not in str(error)


@pytest.mark.parametrize(
    "exception,kind",
    [
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.ConnectError, "connection_error"),
    ],
)
def test_transport_errors_can_be_classified(exception, kind):
    def fail(request):
        raise exception("private-address", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        with create_client(Config(), http=http) as client:
            with pytest.raises(ProviderError) as caught:
                client.generate("Hi")
    assert caught.value.kind == kind
    assert "private-address" not in str(caught.value)


def test_model_catalog_preserves_digest():
    data = {
        "models": [
            {
                "name": "probe",
                "digest": "sha256:probe",
                "size": 42,
                "details": {},
                "capabilities": ["tools"],
            }
        ]
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as http:
        with create_client(Config(), http=http) as client:
            model = client.models()[0]
    assert model["digest"] == "sha256:probe" and model["capabilities"] == ["tools"]


@pytest.mark.parametrize("arguments", ['{"value":1,"value":2}', '{"value":NaN}'])
def test_ambiguous_function_json_is_rejected(openai_client, arguments):
    call = {"type": "function_call", "call_id": "id", "name": "probe", "arguments": arguments}
    client, _ = openai_client([completed(output=[call])])
    with pytest.raises(ProviderError):
        client.generate("Call", tools=[Tool("probe", "Probe")])


def test_editing_ollama_call_does_not_mutate_native_evidence():
    client = ollama_client(
        [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "probe", "arguments": {"value": "original"}}},
                    ]
                },
                "done": True,
            }
        ],
        [],
    )
    first = client.generate("Call", tools=[Tool("probe", "Probe")])
    first.message.tool_calls[0].arguments["value"] = "edited"
    raw = first.message.provider_items[0]["tool_calls"][0]
    assert raw["function"]["arguments"] == {"value": "original"}
    client.http.close()


@pytest.mark.parametrize("call_id", ["", 42])
def test_standalone_openai_invalid_call_ids_are_rejected(openai_client, call_id):
    call = {"type": "function_call", "call_id": call_id, "name": "probe", "arguments": "{}"}
    client, _ = openai_client([completed(output=[call])])
    with pytest.raises(ProviderError):
        client.generate("Call")


def test_duplicate_call_ids_are_rejected_before_execution(openai_client):
    call = {"type": "function_call", "call_id": "same", "name": "probe", "arguments": "{}"}
    client, _ = openai_client([completed(output=[call, call])])
    with pytest.raises(ProviderError):
        client.generate("Call")


def test_unhandled_output_items_are_not_silently_hidden(openai_client):
    output = completed()["response"]["output"] + [{"type": "unhandled_tool_call"}]
    client, _ = openai_client([completed(output=output)])
    with pytest.raises(UnsupportedFeature, match="Unsupported provider output"):
        client.generate("Call")


def test_successful_response_keeps_safe_configuration_and_timing(openai_client):
    records = []
    client, _ = openai_client()
    client.observer = records.append
    response = client.generate("private-prompt")
    assert len(records) == 1 and records[0]["status"] == "completed"
    assert records[0]["native"]["request"]["input"][0]["content"] == "private-prompt"
    summary = response.metadata["request"]
    assert "private-prompt" not in json.dumps(summary)
    assert summary["configuration"]["model_selection"] == "catalog_first"
    assert summary["model"] == "account-model"
    assert summary["elapsed_seconds"] >= summary["inference_http_elapsed_seconds"] >= 0
    assert len(summary["package"]["source_sha256"]) == 64


def test_failed_argument_response_has_raw_evidence_and_no_executable_call(openai_client):
    records = []
    call = {"type": "function_call", "call_id": "id", "name": "probe", "arguments": "not-json"}
    client, _ = openai_client([completed(output=[call])])
    client.observer = records.append
    with pytest.raises(ProviderError) as caught:
        client.generate("Call")
    assert caught.value.attempt_id == records[0]["attempt_id"]
    assert records[0]["status"] == "failed"
    assert "not-json" in records[0]["native"]["raw_events"][-1]
    assert not records[0].get("tool_calls")


def test_cancelled_stream_keeps_partial_evidence_and_closes_transport():
    records = []

    class Partial(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b'{"message":{"content":"partial"},"done":false}\n'
            pytest.fail("Cancellation must not read another event")

        def close(self):
            self.closed = True

    wire = Partial()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=wire))
    ) as http:
        with create_client(Config(), http=http, observer=records.append) as client:
            stream = client.stream("Call")
            assert next(stream).text == "partial"
            stream.close()
    assert wire.closed and records[0]["status"] == "cancelled"
    assert records[0]["interruption"] == "GeneratorExit"
    assert records[0]["native"]["raw_events"]


def test_formal_run_can_require_explicit_model():
    from rosa_llm import ConfigurationError

    with pytest.raises(ConfigurationError, match="OPENAI_MODEL"):
        create_client(Config(provider="openai", require_explicit_model=True))


def test_observer_failure_aborts_the_request(openai_client):
    from rosa_llm import ConfigurationError

    client, _ = openai_client()

    def unavailable_sink(record):
        raise OSError("private-file-path")

    client.observer = unavailable_sink
    with pytest.raises(ConfigurationError, match="observer failed") as caught:
        client.generate("Call")
    assert caught.value.kind == "observer_error"


@pytest.mark.parametrize("counter", [-1, True, "2"])
def test_invalid_token_counts_are_not_reported_as_measurements(openai_client, counter):
    event = completed()
    event["response"]["usage"]["input_tokens"] = counter
    client, _ = openai_client([event])
    with pytest.raises(ProviderError, match="token counts"):
        client.generate("Hi")


def test_unfinished_tool_item_is_not_executable(openai_client):
    from rosa_llm import IncompleteResponse

    call = {
        "type": "function_call",
        "call_id": "id",
        "name": "probe",
        "arguments": "{}",
        "status": "in_progress",
    }
    client, _ = openai_client([completed(output=[call])])
    with pytest.raises(IncompleteResponse):
        client.generate("Call")


def test_observer_has_no_auth_headers_tokens_or_credential_paths(openai_client):
    records = []
    client, _ = openai_client()
    client.observer = records.append
    client.generate("Hi")
    record = json.dumps(records[0])
    for private in (
        "old-access",
        "old-refresh",
        "Authorization",
        str(client.config.credential_dir),
    ):
        assert private not in record


def test_observer_records_incomplete_native_tool_without_releasing_it(openai_client):
    from rosa_llm import IncompleteResponse

    partial = {
        "type": "response.output_item.done",
        "output_index": 0,
        "item": {
            "type": "function_call",
            "name": "probe",
            "call_id": "id",
            "arguments": '{"x":1}',
        },
    }
    client, _ = openai_client([partial])
    records = []
    client.observer = records.append
    with pytest.raises(IncompleteResponse):
        client.generate("Call")
    assert records[0]["status"] == "failed"
    assert '"name": "probe"' in records[0]["native"]["raw_events"][0]
    assert records[0].get("tool_calls", []) == []


def test_observer_receives_independent_json_serializable_records(openai_client):
    records = []
    client, _ = openai_client()
    client.observer = records.append
    for _ in range(2):
        response = client.generate("Hi")
    assert records[0]["attempt_id"] != records[1]["attempt_id"]
    records[0]["configuration"]["model"] = "changed"
    assert records[1]["configuration"]["model"] is None
    assert response.metadata["request"]["configuration"]["model"] is None
    json.dumps(records)


def test_tool_edits_survive_standalone_openai_history_replay(openai_client):
    call = {"type": "function_call", "call_id": "id", "name": "probe", "arguments": '{"x":1}'}
    client, requests = openai_client([completed(output=[call])])
    first = client.generate("Call")
    first.message.tool_calls[0].arguments["x"] = 2
    client.generate([first.message, Message("tool", "result", tool_call_id="id")])
    sent = json.loads(requests[-1].content)["input"][0]
    assert json.loads(sent["arguments"]) == {"x": 2}
    assert first.message.provider_items[0]["arguments"] == '{"x":1}'


def test_unchanged_standalone_history_preserves_opaque_reasoning(openai_client):
    output = [
        {"type": "reasoning", "encrypted_content": "opaque"},
        *completed()["response"]["output"],
    ]
    client, requests = openai_client([completed(output=output)])
    first = client.generate("Hi")
    history = deepcopy(first.message)
    client.generate([history, Message("user", "Continue")])
    assert json.loads(requests[-1].content)["input"][:2] == output


def test_explicit_model_flag_environment_and_cli_override(monkeypatch, tmp_path, capsys):
    from dataclasses import replace

    from rosa_llm import cli

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ROSA_LLM_REQUIRE_EXPLICIT_MODEL", "true")
    monkeypatch.setenv("ROSA_LLM_AUTH_DIR", str(tmp_path / "auth"))
    config = Config.load()
    assert config.require_explicit_model
    with create_client(replace(config, openai_model="selected")):
        pass
    # Status permits the override without contacting a provider or starting OAuth.
    assert cli.main(["status", "--model", "selected", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "selected"


def test_error_metadata_accepts_only_bounded_identifier_fields():
    response = httpx.Response(
        400,
        headers={"x-request-id": "req_valid"},
        json={
            "error": {
                "code": "bad_input",
                "param": "tools[0].parameters",
                "message": "private",
            }
        },
    )
    with pytest.raises(ProviderError) as caught:
        check_response(response)
    assert caught.value.param == "tools[0].parameters"
    assert "private" not in json.dumps(caught.value.to_dict())


def test_cli_preserves_safe_http_diagnostics(monkeypatch, capsys):
    from rosa_llm import cli

    from .test_cli import mock_ollama

    mock_ollama(monkeypatch, status=500)
    assert cli.main(["ask", "Hi", "--json"]) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["status_code"] == 500 and error["kind"] == "http_error"


def test_bad_request_json_is_a_configuration_error_before_network():
    from rosa_llm import ConfigurationError

    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: requests.append(request))
    ) as http:
        with create_client(Config(), http=http) as client:
            with pytest.raises(ConfigurationError, match="JSON values"):
                client.generate("Call", tools=[Tool("probe", "Probe", {"default": float("nan")})])
    assert requests == []


def test_concurrent_async_langchain_requests_keep_separate_records(model_factory):
    import asyncio

    records = []
    model, _ = model_factory([[completed()] for _ in range(4)])
    model.client.observer = records.append
    results = asyncio.run(model.abatch(["a", "b", "c", "d"]))
    assert len(records) == 4
    ids = {record["attempt_id"] for record in records}
    assert len(ids) == 4
    assert {result.response_metadata["request"]["attempt_id"] for result in results} == ids
    assert all(record["status"] == "completed" for record in records)


def test_async_stream_close_records_cancellation(model_factory):
    import asyncio

    records = []
    model, _ = model_factory([[{"type": "response.output_text.delta", "delta": "partial"}]])
    model.client.observer = records.append

    async def run():
        stream = model.astream("Call")
        assert (await anext(stream)).content == "partial"
        await stream.aclose()
        # The pinned LangChain worker bridge finalizes its sync iterator on the loop.
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(records) == 1 and records[0]["status"] == "cancelled"


@pytest.mark.parametrize("arguments", ['{"x":Infinity}', '{"x":1e999}'])
def test_nonfinite_arguments_fail_even_in_native_ollama_envelopes(arguments):
    chunks = [
        {
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "probe",
                            "arguments": arguments,
                        }
                    }
                ]
            },
            "done": True,
        }
    ]
    client = ollama_client(chunks, [])
    with pytest.raises(ProviderError):
        client.generate("Call")
    client.http.close()


def test_standalone_stream_rejects_conflicting_terminal_text(openai_client):
    client, _ = openai_client(
        [
            {"type": "response.output_text.delta", "delta": "wrong"},
            completed(),
        ]
    )
    with pytest.raises(ProviderError, match="does not match"):
        list(client.stream("Hi"))


def test_cli_stream_prints_text_present_only_in_terminal_snapshot(
    openai_client, monkeypatch, capsys
):
    from rosa_llm import cli

    client, _ = openai_client([completed()])
    monkeypatch.setattr(cli, "create_client", lambda config: client)
    assert cli.main(["ask", "Hi", "--provider", "openai", "--stream"]) == 0
    assert capsys.readouterr().out == "Hello\n"


def test_terminal_snapshot_cannot_hide_a_completed_native_tool(openai_client):
    call = {"type": "function_call", "call_id": "id", "name": "probe", "arguments": "{}"}
    client, _ = openai_client(
        [
            {"type": "response.output_item.done", "output_index": 0, "item": call},
            completed(),
        ]
    )
    with pytest.raises(ProviderError, match="snapshot"):
        client.generate("Call")


def test_injected_http_timeouts_are_recorded():
    with httpx.Client(
        timeout=3.25,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"message": {"content": "Hi"}, "done": True}),
        ),
    ) as http:
        with create_client(Config(timeout=120), http=http) as client:
            summary = client.generate("Hi").metadata["request"]
    assert summary["transport"]["timeouts"]["read"] == 3.25
    assert summary["transport"]["provided_by_caller"] is True


def test_oauth_never_follows_token_redirect_with_injected_client(tmp_path):
    from rosa_llm.auth import OAuthManager
    from rosa_llm.storage import CredentialStore

    redirected_requests = []

    def handler(request):
        if request.url.host == "auth.openai.com":
            return httpx.Response(307, headers={"location": "https://untrusted.invalid/tokens"})
        redirected_requests.append(request)
        return httpx.Response(200, json={"access_token": "test-only"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        auth = OAuthManager(CredentialStore(tmp_path / "auth"), http)
        with pytest.raises(ProviderError):
            auth._tokens({"grant_type": "refresh_token", "refresh_token": "test-only-refresh"})
    assert redirected_requests == []
