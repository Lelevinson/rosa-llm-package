"""Configuration precedence: defaults < user config < local .env < process environment."""

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from .errors import ConfigurationError


def user_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rosa-llm"


@dataclass(frozen=True)
class Config:
    provider: str = "ollama"
    ollama_model: str = "qwen3:8b"
    ollama_base_url: str = "http://localhost:11434"
    openai_model: str = ""
    auth_dir: Path | None = None
    profile: str = "default"
    timeout: float = 120
    require_explicit_model: bool = False

    def __post_init__(self):
        if self.provider not in {"ollama", "openai"}:
            raise ConfigurationError("LLM_PROVIDER must be ollama or openai.")
        if self.provider == "ollama" and not self.ollama_model.strip():
            raise ConfigurationError("OLLAMA_MODEL must not be empty.")
        try:
            url = urlsplit(self.ollama_base_url)
            _ = url.port
        except ValueError:
            raise ConfigurationError("OLLAMA_BASE_URL contains an invalid host or port.") from None
        if any(char.isspace() or ord(char) < 32 for char in self.ollama_base_url):
            raise ConfigurationError(
                "OLLAMA_BASE_URL cannot contain whitespace or control characters."
            )
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ConfigurationError("OLLAMA_BASE_URL must be an HTTP(S) URL without credentials.")
        if url.query or url.fragment:
            raise ConfigurationError("OLLAMA_BASE_URL must not contain a query or fragment.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.profile):
            raise ConfigurationError(
                "Profile must contain 1-64 letters, digits, underscores or dashes."
            )
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ConfigurationError("ROSA_LLM_TIMEOUT must be a positive number.")
        if not isinstance(self.require_explicit_model, bool):
            raise ConfigurationError("require_explicit_model must be a boolean.")

    def to_dict(self) -> dict:
        """Effective nonsecret configuration, suitable for a run manifest."""
        return {
            "provider": self.provider,
            "model": self.model or None,
            "model_selection": "catalog_first" if not self.model else "explicit",
            "endpoint": self.ollama_base_url
            if self.provider == "ollama"
            else "https://api.openai.com/v1",
            "profile": self.profile,
            "timeout_seconds": self.timeout,
            "timeout_scope": "http_phase",
            "require_explicit_model": self.require_explicit_model,
        }

    @property
    def model(self) -> str:
        return self.ollama_model if self.provider == "ollama" else self.openai_model

    @property
    def credential_dir(self) -> Path:
        return (self.auth_dir or user_config_dir()).expanduser().resolve()

    @classmethod
    def load(cls, env_file: Path | str | None = None, *, user_file: Path | None = None):
        values = dict(
            dotenv_values(user_file or user_config_dir() / "config.env", interpolate=False)
        )
        # Do not mutate os.environ: separate clients can load separate configurations.
        values.update(dotenv_values(env_file or Path.cwd() / ".env", interpolate=False))
        values.update(os.environ)
        explicit_model = (values.get("ROSA_LLM_REQUIRE_EXPLICIT_MODEL") or "false").lower()
        if explicit_model not in {"true", "false", "1", "0"}:
            raise ConfigurationError("ROSA_LLM_REQUIRE_EXPLICIT_MODEL must be true or false.")
        try:
            return cls(
                provider=values.get("LLM_PROVIDER") or "ollama",
                ollama_model=values.get("OLLAMA_MODEL", "qwen3:8b") or "",
                ollama_base_url=(values.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip(
                    "/"
                ),
                openai_model=values.get("OPENAI_MODEL") or "",
                auth_dir=Path(values["ROSA_LLM_AUTH_DIR"])
                if values.get("ROSA_LLM_AUTH_DIR")
                else None,
                profile=values.get("ROSA_LLM_PROFILE") or "default",
                timeout=float(values.get("ROSA_LLM_TIMEOUT") or 120),
                require_explicit_model=explicit_model in {"true", "1"},
            )
        except (ValueError, TypeError):
            raise ConfigurationError(
                "Invalid configuration value; check URLs and timeout."
            ) from None
