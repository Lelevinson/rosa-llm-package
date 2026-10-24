"""Small provider-independent value objects. Tool execution belongs to the caller."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .errors import ConfigurationError, UnsupportedFeature


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    kind: Literal["function", "custom"] = "function"
    namespace: str = "local"
    format: dict[str, Any] | None = None
    strict: bool | None = None

    def __post_init__(self):
        import re

        if self.kind not in {"function", "custom"}:
            raise ConfigurationError("Tool kind must be function or custom.")
        for name in (self.name, self.namespace):
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise ConfigurationError(
                    "Tool and namespace names require 1-64 letters/digits/_/-."
                )
        if self.strict is not None and not isinstance(self.strict, bool):
            raise ConfigurationError("Tool strict must be a boolean or None.")
        if self.kind != "function" and self.strict is not None:
            raise ConfigurationError("Only function tools accept strict schemas.")
        if not isinstance(self.parameters, dict):
            raise ConfigurationError("Tool parameters must be a JSON schema object.")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | str
    kind: str = "function"
    namespace: str | None = None


@dataclass
class Message:
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_kind: str = "function"
    # Retain complete provider output, including reasoning items, for stateless continuation.
    provider: str | None = None
    provider_items: list[dict[str, Any]] = field(default_factory=list, repr=False)
    provider_snapshot: dict[str, Any] | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ConfigurationError("Unsupported message role.")
        if self.role == "tool" and not self.tool_call_id:
            raise ConfigurationError("Tool results require tool_call_id.")
        if not isinstance(self.content, str):
            raise ConfigurationError("Message content must be text.")
        if self.provider_items and self.provider_snapshot is None:
            self.provider_snapshot = deepcopy(self._visible_fields())

    def _visible_fields(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "tool_kind": self.tool_kind,
        }

    def native_history(self, provider: str) -> list[dict]:
        """Replay opaque state only while its associated public message is unchanged."""
        if not self.provider_items:
            return []
        if self.provider != provider:
            raise UnsupportedFeature("Provider-specific history cannot cross providers.")
        if self.provider_snapshot != self._visible_fields():
            return []
        return self.provider_items


@dataclass
class Response:
    provider: str
    model: str
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    message: Message | None = None

    def to_dict(self) -> dict[str, Any]:
        # Raw provider history is accessible via message, not duplicated in CLI JSON.
        return {
            "provider": self.provider,
            "model": self.model,
            "response": self.text,
            "tool_calls": [asdict(call) for call in self.tool_calls],
            "usage": self.usage,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class StreamEvent:
    type: Literal["text", "completed"]
    text: str = ""
    response: Response | None = None
