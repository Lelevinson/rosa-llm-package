"""Public API for the standalone rosa-llm package."""

from .client import Client, create_client
from .config import Config
from .errors import (
    AuthenticationError,
    ConfigurationError,
    IncompleteResponse,
    LLMError,
    PermissionDenied,
    ProviderError,
    UnsupportedFeature,
    UsageLimitError,
)
from .provenance import package_info
from .types import Message, Response, StreamEvent, Tool, ToolCall

__all__ = [
    "AuthenticationError",
    "Client",
    "Config",
    "ConfigurationError",
    "IncompleteResponse",
    "LLMError",
    "Message",
    "PermissionDenied",
    "ProviderError",
    "Response",
    "StreamEvent",
    "Tool",
    "ToolCall",
    "UnsupportedFeature",
    "UsageLimitError",
    "create_client",
    "package_info",
]
