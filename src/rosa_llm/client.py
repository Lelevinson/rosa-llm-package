"""Shared API with explicit history and streaming. No hidden agent loop or provider fallback."""

from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

import httpx

from .auth import OAuthManager
from .config import Config
from .errors import ConfigurationError, IncompleteResponse, LLMError, ProviderError
from .http import transport_error
from .provenance import package_info
from .providers import OllamaProvider, OpenAIProvider
from .storage import CredentialStore
from .types import Message, Response, StreamEvent, Tool


class Client:
    def __init__(
        self,
        config: Config,
        *,
        http: httpx.Client | None = None,
        observer: Callable[[dict], None] | None = None,
    ):
        self.config = config
        if (
            config.require_explicit_model
            and config.provider == "openai"
            and not config.openai_model
        ):
            raise ConfigurationError("Set OPENAI_MODEL when explicit model selection is required.")
        self.observer = observer
        self._package_info = package_info()
        self._owns_http = http is None
        self.http = http or httpx.Client(
            timeout=config.timeout, follow_redirects=False, trust_env=False
        )
        if config.provider == "ollama":
            self.provider = OllamaProvider(config, self.http)
        else:
            credential_store = CredentialStore(config.credential_dir)
            auth = OAuthManager(credential_store, self.http, config.profile)
            self.provider = OpenAIProvider(config, self.http, auth)

    def close(self):
        if self._owns_http:
            self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def models(self) -> list[dict]:
        try:
            return self.provider.models()
        except httpx.HTTPError as error:
            raise transport_error(error) from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError("Provider returned a malformed model catalog.") from None

    def stream(
        self, prompt: str | Sequence[Message], *, tools: Sequence[Tool] = (), **options
    ) -> Iterator[StreamEvent]:
        messages = [Message("user", prompt)] if isinstance(prompt, str) else list(prompt)
        if not messages or not all(isinstance(m, Message) for m in messages):
            raise ConfigurationError(
                "Pass a prompt string or a nonempty sequence of Message objects."
            )
        if not all(isinstance(tool, Tool) for tool in tools):
            raise ConfigurationError("Tools must be Tool objects.")
        started = perf_counter()
        observer = self.observer
        record = {
            "schema_version": 1,
            "attempt_id": str(uuid4()),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "configuration": self.config.to_dict(),
            "transport": {
                "provided_by_caller": not self._owns_http,
                "timeouts": self.http.timeout.as_dict(),
                "follow_redirects": False,
            },
            "package": deepcopy(self._package_info),
            "status": "cancelled",
            "time_to_first_text_seconds": None,
        }
        if observer is not None:
            record["messages"] = [asdict(message) for message in messages]
            record["tools"] = [asdict(tool) for tool in tools]
            record["options"] = deepcopy(options)
        native = {"capture": observer is not None}
        ended = None
        streamed_text = ""
        try:
            with closing(
                self.provider.stream(messages, list(tools), options, trace=native)
            ) as events:
                for event in events:
                    if event.type == "text":
                        streamed_text += event.text
                    if event.type == "text" and record["time_to_first_text_seconds"] is None:
                        record["time_to_first_text_seconds"] = perf_counter() - started
                    if event.type == "completed" and event.response is not None:
                        response = event.response
                        if not response.text.startswith(streamed_text):
                            raise ProviderError("Completed text does not match the streamed text.")
                        if not isinstance(response.model, str) or not response.model.strip():
                            raise ProviderError("Provider returned an invalid model identity.")
                        call_ids = set()
                        for call in response.tool_calls:
                            if (
                                not isinstance(call.id, str)
                                or not call.id
                                or call.id in call_ids
                                or not isinstance(call.name, str)
                                or not call.name
                            ):
                                raise ProviderError(
                                    "Provider tool call IDs/names must be nonempty and IDs unique.",
                                    kind="invalid_tool_call",
                                )
                            call_ids.add(call.id)
                        if not isinstance(response.usage, dict):
                            raise ProviderError("Provider returned invalid usage metadata.")
                        for key in (
                            "input_tokens",
                            "output_tokens",
                            "total_tokens",
                            "prompt_eval_count",
                            "eval_count",
                        ):
                            if key in response.usage:
                                count = response.usage[key]
                                if type(count) is not int or count < 0:
                                    raise ProviderError("Provider returned invalid token counts.")
                        for key, counter in (
                            ("input_tokens_details", "cached_tokens"),
                            ("output_tokens_details", "reasoning_tokens"),
                        ):
                            details = response.usage.get(key)
                            if details is not None:
                                if not isinstance(details, dict):
                                    raise ProviderError("Provider returned invalid token details.")
                                if counter in details:
                                    count = details[counter]
                                    if type(count) is not int or count < 0:
                                        raise ProviderError(
                                            "Provider returned invalid token details."
                                        )
                        if not response.text.strip() and not response.tool_calls:
                            raise IncompleteResponse(
                                "Provider completed without text or a tool request."
                            )
                        ended = perf_counter()
                        record["status"] = "completed"
                        record["model"] = response.model
                        record["usage"] = deepcopy(response.usage)
                        record["tool_calls"] = [asdict(call) for call in response.tool_calls]
                        record["ended_at"] = datetime.now(timezone.utc).isoformat()
                        summary = {
                            key: deepcopy(record[key])
                            for key in (
                                "schema_version",
                                "attempt_id",
                                "started_at",
                                "configuration",
                                "transport",
                                "package",
                                "status",
                                "ended_at",
                                "time_to_first_text_seconds",
                                "model",
                            )
                        }
                        summary["elapsed_seconds"] = ended - started
                        if "_http_started" in native:
                            summary["inference_http_elapsed_seconds"] = (
                                ended - native["_http_started"]
                            )
                            summary["setup_elapsed_seconds"] = native["_http_started"] - started
                        response.metadata["request"] = summary
                    yield event
        except httpx.HTTPError as cause:
            error = transport_error(cause)
            error.attempt_id = record["attempt_id"]
            record["status"] = "failed"
            record["error"] = error.to_dict()
            raise error from None
        except LLMError as error:
            error.attempt_id = record["attempt_id"]
            record["status"] = "failed"
            record["error"] = error.to_dict()
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            error = ProviderError(
                "Provider returned malformed response or tool-call data.", kind="malformed_response"
            )
            error.attempt_id = record["attempt_id"]
            record["status"] = "failed"
            record["error"] = error.to_dict()
            raise error from None
        except BaseException as error:
            if record["status"] != "completed":
                record["status"] = "cancelled"
                record["interruption"] = type(error).__name__
            raise
        finally:
            if ended is None:
                ended = perf_counter()
            record["elapsed_seconds"] = ended - started
            if "_http_started" in native:
                http_started = native.pop("_http_started")
                record["inference_http_elapsed_seconds"] = ended - http_started
                record["setup_elapsed_seconds"] = http_started - started
            record.setdefault("ended_at", datetime.now(timezone.utc).isoformat())
            if observer is not None:
                record["native"] = native
                try:
                    observer(deepcopy(record))
                except Exception:
                    raise ConfigurationError(
                        "Request observer failed; experiment evidence may not have been saved.",
                        kind="observer_error",
                        attempt_id=record["attempt_id"],
                    ) from None

    def generate(
        self, prompt: str | Sequence[Message], *, tools: Sequence[Tool] = (), **options
    ) -> Response:
        with closing(self.stream(prompt, tools=tools, **options)) as events:
            for event in events:
                if event.type == "completed" and event.response is not None:
                    return event.response
        raise IncompleteResponse("No completed response was received.")


def create_client(
    config: Config | None = None,
    *,
    env_file: str | None = None,
    http: httpx.Client | None = None,
    observer: Callable[[dict], None] | None = None,
) -> Client:
    return Client(config or Config.load(env_file), http=http, observer=observer)
