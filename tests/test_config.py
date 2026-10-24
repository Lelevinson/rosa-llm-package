import pytest

from rosa_llm import Config, ConfigurationError


def test_dotenv_loading_switching_and_precedence(tmp_path, monkeypatch):
    local = tmp_path / ".env"
    user = tmp_path / "user.env"
    user.write_text("LLM_PROVIDER=openai\nOPENAI_MODEL=user-model\n")
    local.write_text("LLM_PROVIDER=ollama\nOLLAMA_MODEL=local-model\n")
    config = Config.load(local, user_file=user)
    assert config.provider == "ollama" and config.model == "local-model"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "process-model")
    assert Config.load(local, user_file=user).model == "process-model"
    assert "OLLAMA_MODEL" not in __import__("os").environ


def test_cwd_env_and_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from rosa_llm.config import user_config_dir

    user_config_dir().mkdir(parents=True)
    (user_config_dir() / "config.env").write_text("LLM_PROVIDER=openai\nOPENAI_MODEL=user-model\n")
    assert Config.load().model == "user-model"
    (tmp_path / ".env").write_text("OPENAI_MODEL=local-model\n")
    assert Config.load().model == "local-model"


@pytest.mark.parametrize(
    "settings",
    [
        {"provider": "unknown"},
        {"ollama_model": ""},
        {"timeout": -1},
        {"timeout": float("nan")},
        {"profile": "../bad"},
        {"ollama_base_url": "file:///etc/passwd"},
        {"ollama_base_url": "https://user:secret@example.com"},
        {"ollama_base_url": "http://localhost/?token=secret"},
        {"ollama_base_url": "http://localhost:invalid"},
        {"ollama_base_url": "http://localhost:99999"},
        {"ollama_base_url": "http://[broken"},
    ],
)
def test_invalid_config(settings):
    with pytest.raises(ConfigurationError):
        Config(**settings)


def test_bad_timeout_env(monkeypatch):
    monkeypatch.setenv("ROSA_LLM_TIMEOUT", "secret-invalid-value")
    with pytest.raises(ConfigurationError, match="Invalid configuration") as error:
        Config.load()
    assert "secret-invalid-value" not in str(error.value)


def test_auth_override_and_auto_openai_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ROSA_LLM_AUTH_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    config = Config.load()
    assert config.credential_dir == tmp_path / "private"
    assert config.model == ""
