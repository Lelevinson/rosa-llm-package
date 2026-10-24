import json

import httpx
import pytest

from rosa_llm import Config, cli, create_client


def test_initialize_does_not_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--provider", "openai", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "openai"
    original = (tmp_path / ".env").read_text()
    assert cli.main(["init", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "configuration_error"
    assert (tmp_path / ".env").read_text() == original


def test_status_and_provider_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["provider"] == "ollama" and not data["authentication_required"]
    assert cli.main(["status", "--provider", "openai", "--profile", "research", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert not data["authenticated"] and data["profile"] == "research"
    assert "access_token" not in data


def mock_ollama(monkeypatch, *, text="Hello", status=200):
    requests = []

    def create(config):
        def dispatch(request):
            requests.append(request)
            return httpx.Response(
                status,
                json={
                    "done": True,
                    "model": config.model,
                    "message": {"content": text},
                    "eval_count": 1,
                },
            )

        return create_client(config, http=httpx.Client(transport=httpx.MockTransport(dispatch)))

    monkeypatch.setattr(cli, "create_client", create)
    return requests


def test_json_output_and_model_override(monkeypatch, capsys):
    seen = mock_ollama(monkeypatch)
    assert cli.main(["ask", "Say hello", "--model", "chosen-model", "--json"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert data["response"] == "Hello" and data["model"] == "chosen-model"
    assert data["provider"] == "ollama" and data["usage"]["eval_count"] == 1
    assert not output.err
    assert json.loads(seen[0].content)["model"] == "chosen-model"


def test_json_failure_and_exit_code(monkeypatch, capsys):
    mock_ollama(monkeypatch, status=429)
    assert cli.main(["ask", "hello", "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "usage_limit"
    assert output.err


@pytest.mark.parametrize(
    "args",
    [
        ["ask", "--json"],
        ["unknown", "--json"],
        ["ask", "hello", "--json", "--stream"],
        ["status", "--provider", "invalid", "--json"],
    ],
)
def test_argument_errors_are_json(monkeypatch, capsys, args):
    mock_ollama(monkeypatch)
    assert cli.main(args) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "configuration_error"


def test_unattended_openai_test_never_starts_login(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ROSA_LLM_AUTH_DIR", str(tmp_path / "auth"))
    monkeypatch.setattr(
        "rosa_llm.auth.OAuthManager.login", lambda *a, **kw: pytest.fail("browser login attempted")
    )
    assert cli.main(["test", "--provider", "openai", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "authentication_required"


def test_live_smoke_verifies_actual_answer(monkeypatch, capsys):
    mock_ollama(monkeypatch, text="ROSA_LLM_OK")
    assert cli.main(["test", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metadata"]["smoke_test"] == "passed"
    mock_ollama(monkeypatch, text="wrong answer")
    assert cli.main(["test", "--json"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_stream_text(monkeypatch, capsys):
    mock_ollama(monkeypatch)
    assert cli.main(["ask", "hello", "--stream"]) == 0
    assert capsys.readouterr().out.strip() == "Hello"


def test_tool_smoke_does_not_execute_unexpected_tool(monkeypatch):
    from rosa_llm import Message, ProviderError, Response, ToolCall

    class UnexpectedClient:
        def generate(self, *args, **kwargs):
            return Response(
                "ollama",
                "model",
                "",
                [ToolCall("1", "dangerous", {})],
                message=Message("assistant"),
            )

    with pytest.raises(ProviderError, match="unexpected call"):
        cli.smoke_test(UnexpectedClient(), True)


def test_per_user_initialization(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", "--user", "--json"]) == 0
    path = json.loads(capsys.readouterr().out)["initialized"]
    assert path.endswith("/rosa-llm/config.env")
    assert Config.load().provider == "ollama"
