"""Optional LangChain 0.3 adapter. Importing rosa_llm alone does not import LangChain."""

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from copy import deepcopy
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_function, convert_to_openai_tool
from pydantic import BaseModel, Field, PrivateAttr

from .client import Client
from .errors import ConfigurationError, IncompleteResponse, ProviderError, UnsupportedFeature
from .types import Message, Response, Tool, ToolCall

HISTORY_KEY = "rosa_llm_history"


def _text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    pieces = []
    for block in content:
        if isinstance(block, str):
            pieces.append(block)
        elif (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            pieces.append(block["text"])
        else:
            raise UnsupportedFeature("ChatRosaLLM accepts text content only.")
    return "".join(pieces)


class ChatRosaLLM(BaseChatModel):
    """Translate LangChain messages/tools to rosa-llm without executing tools.

    The caller owns the Client and closes it after all model/agent calls finish.
    Optional allowed_tools limits both offered tools and accepted model requests.
    Async invoke/stream use BaseChatModel's worker-thread implementation.
    """

    client: Client = Field(exclude=True, repr=False)
    allowed_tools: frozenset[str] | None = None
    _cache_scope: str = PrivateAttr(default_factory=lambda: str(uuid4()))

    @property
    def _history_scope(self) -> str:
        config = self.client.config
        # Partition opaque state without putting credential paths or credentials in traces.
        identity = [
            config.provider,
            config.model,
            config.ollama_base_url,
            str(config.credential_dir),
            config.profile,
        ]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    @property
    def _llm_type(self) -> str:
        return "rosa-llm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        config = self.client.config
        return {
            "provider": config.provider,
            "model": config.model or "account-catalog",
            "profile": config.profile,
            "client_scope": self._cache_scope,
            "allowed_tools": sorted(self.allowed_tools) if self.allowed_tools is not None else None,
        }

    def model_post_init(self, context: Any):
        super().model_post_init(context)
        # A dynamically selected account catalog should not use a process-global cache.
        if self.cache is None:
            self.cache = False

    def _tools(self, tools: Sequence, *, strict: bool | None = None) -> list[Tool]:
        converted = []
        for definition in tools:
            if isinstance(definition, Tool):
                if (
                    definition.kind != "function"
                    or definition.namespace != "local"
                    or definition.format is not None
                ):
                    raise UnsupportedFeature("ChatRosaLLM supports local JSON function tools only.")
                function = {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                }
                if definition.strict is not None:
                    function["strict"] = definition.strict
            else:
                # Check explicit OpenAI function fields before upstream conversion can
                # discard them. JSON Schema / other supported formats use its normal rules.
                if isinstance(definition, dict) and (
                    definition.get("type") == "function"
                    or ("name" in definition and "input_schema" not in definition)
                ):
                    function = definition.get("function", definition)
                    if set(function) - {"name", "description", "parameters", "strict", "type"}:
                        raise UnsupportedFeature("Unsupported function definition fields.")
                schema = convert_to_openai_tool(definition)
                if "function" in schema and set(schema) - {"type", "function"}:
                    raise UnsupportedFeature("Unsupported fields on a function tool wrapper.")
                if schema.get("type") != "function":
                    raise UnsupportedFeature("ChatRosaLLM supports JSON function tools only.")
                function = schema.get("function", schema)
            # Existing tool dictionaries bypass strict conversion upstream. Convert the
            # function itself on a copy so explicit strict has the same semantics for all inputs.
            function = convert_to_openai_function(deepcopy(function), strict=strict)
            tool = Tool(
                function["name"],
                function.get("description", ""),
                deepcopy(function.get("parameters", {"type": "object", "properties": {}})),
                strict=function.get("strict"),
            )
            if self.client.config.provider == "ollama" and tool.strict is not None:
                raise UnsupportedFeature("Ollama has no strict function-schema request option.")
            if self.allowed_tools is None or tool.name in self.allowed_tools:
                converted.append(tool)
        names = [tool.name for tool in converted]
        if len(names) != len(set(names)):
            raise ConfigurationError("Bound tool names must be unique.")
        if tools and self.allowed_tools is not None and not self.allowed_tools.issubset(names):
            raise ConfigurationError(
                "An allowed tool is missing from the supplied tool definitions."
            )
        return converted

    def bind_tools(
        self,
        tools: Sequence[dict | type[BaseModel] | Callable | BaseTool],
        *,
        tool_choice: str | dict | bool | None = None,
        strict: bool | None = None,
        parallel_tool_calls: bool | None = None,
        **kwargs: Any,
    ) -> Runnable:
        converted_tools = self._tools(tools, strict=strict)
        formatted_tools = []
        for tool in converted_tools:
            function = {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            if tool.strict is not None:
                function["strict"] = tool.strict
            formatted_tools.append({"type": "function", "function": function})
        # Normalize the common LangChain choice spellings, not argument values.
        choice = tool_choice
        if choice is True or choice == "any":
            choice = "required"
        elif choice is False:
            choice = None
        if choice is not None and choice not in ("auto", "none", "required"):
            raise UnsupportedFeature(
                "Named tool_choice is unsupported with this OAuth namespace path; "
                "use auto, none or required."
            )
        if self.client.config.provider == "ollama":
            if choice not in (None, "auto") or parallel_tool_calls is not None:
                raise UnsupportedFeature(
                    "Ollama does not expose tool_choice or parallel_tool_calls."
                )
        else:
            if choice is not None:
                kwargs["tool_choice"] = choice
            if parallel_tool_calls is not None:
                if not isinstance(parallel_tool_calls, bool):
                    raise ConfigurationError("parallel_tool_calls must be a boolean.")
                kwargs["parallel_tool_calls"] = parallel_tool_calls
        # The BaseChatModel consumes ls_structured_output_format for tracing, not the API.
        if self.client.config.provider == "ollama":
            supported_options = {
                "options",
                "think",
                "format",
                "keep_alive",
                "ls_structured_output_format",
            }
        else:
            supported_options = {
                "tool_choice",
                "parallel_tool_calls",
                "ls_structured_output_format",
            }
        unsupported = set(kwargs) - supported_options
        if unsupported:
            raise UnsupportedFeature(
                "Unsupported bind_tools options: " + ", ".join(sorted(unsupported))
            )
        return self.bind(tools=formatted_tools, **kwargs)

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        # The pinned base implementation silently drops method/strict. Reject instead.
        method = kwargs.pop("method", "function_calling")
        if method != "function_calling" or kwargs:
            raise UnsupportedFeature(
                "with_structured_output supports function_calling and include_raw only."
            )
        return super().with_structured_output(schema, include_raw=include_raw)

    def _messages(self, messages: list[BaseMessage]) -> list[Message]:
        converted = []
        call_names: dict[str, str] = {}
        for message in messages:
            text = _text(message.content)
            if isinstance(message, AIMessage):
                if "function_call" in message.additional_kwargs:
                    raise UnsupportedFeature(
                        "Legacy function_call messages are unsupported; use tool_calls."
                    )
                if message.invalid_tool_calls:
                    raise ConfigurationError(
                        "Cannot replay a message containing invalid tool calls."
                    )
                calls = []
                for call in message.tool_calls:
                    if not call.get("id"):
                        raise ConfigurationError("Tool calls require an ID for result correlation.")
                    if call["id"] in call_names:
                        raise ConfigurationError("Tool call IDs must be unique within a history.")
                    call_names[call["id"]] = call["name"]
                    calls.append(
                        ToolCall(
                            call["id"], call["name"], deepcopy(call["args"]), namespace="local"
                        )
                    )
                history = message.additional_kwargs.get(HISTORY_KEY)
                if history is not None:
                    if (
                        not isinstance(history, dict)
                        or history.get("provider") != self.client.config.provider
                        or history.get("scope") != self._history_scope
                        or history.get("version") != 1
                        or not isinstance(history.get("items"), list)
                    ):
                        raise UnsupportedFeature(
                            "Saved provider history does not match this client."
                        )
                    if history.get("visible") != {
                        "content": text,
                        "tool_calls": message.tool_calls,
                    }:
                        # Message transforms/edits are authoritative. Old reasoning and call
                        # snapshots cannot override the caller's current public fields.
                        converted.append(Message("assistant", text, calls))
                        continue
                    converted.append(
                        Message(
                            "assistant",
                            text,
                            calls,
                            provider=history["provider"],
                            provider_items=deepcopy(history["items"]),
                        )
                    )
                else:
                    converted.append(Message("assistant", text, calls))
            elif isinstance(message, ToolMessage):
                name = (
                    message.name
                    or message.additional_kwargs.get("name")
                    or call_names.get(message.tool_call_id)
                )
                previous_name = call_names.get(message.tool_call_id)
                if previous_name and name != previous_name:
                    raise ConfigurationError("Tool result name conflicts with its earlier call ID.")
                if not name and self.client.config.provider == "ollama":
                    raise ConfigurationError(
                        "A tool result requires a name or a matching earlier tool call."
                    )
                converted.append(
                    Message("tool", text, tool_call_id=message.tool_call_id, tool_name=name)
                )
            elif isinstance(message, HumanMessage):
                converted.append(Message("user", text))
            elif isinstance(message, SystemMessage):
                converted.append(Message("system", text))
            elif isinstance(message, ChatMessage) and message.role in {
                "developer",
                "system",
                "user",
                "assistant",
            }:
                converted.append(Message(message.role, text))
            else:
                raise UnsupportedFeature(
                    "This LangChain message type is unsupported by ChatRosaLLM."
                )
        return converted

    def _request(self, messages: list[BaseMessage], stop: list[str] | None, kwargs: dict):
        options = dict(kwargs)
        if stop is not None:
            if self.client.config.provider != "ollama":
                raise UnsupportedFeature("OpenAI Responses does not expose stop sequences.")
            native_options = deepcopy(options.get("options", {}))
            if not isinstance(native_options, dict) or "stop" in native_options:
                raise ConfigurationError("Pass stop once, as a LangChain stop or options.stop.")
            native_options["stop"] = stop
            options["options"] = native_options
        tools = self._tools(options.pop("tools", []))
        # LangChain's presentation flag does not change the provider's wire protocol.
        if "stream" in options:
            stream = options.pop("stream")
            if not isinstance(stream, bool):
                raise ConfigurationError("stream must be a boolean.")
        return self._messages(messages), tools, options

    def _message(self, response: Response) -> AIMessage:
        calls = []
        seen = set()
        for call in response.tool_calls:
            if (
                call.kind != "function"
                or call.namespace not in (None, "local")
                or not isinstance(call.name, str)
                or not call.name
                or (self.allowed_tools is not None and call.name not in self.allowed_tools)
                or not isinstance(call.arguments, dict)
            ):
                raise ProviderError(
                    "Model requested a tool outside the supported function contract or allowlist."
                )
            if not isinstance(call.id, str) or not call.id or call.id in seen:
                raise ProviderError("Provider tool call IDs must be nonempty and unique.")
            seen.add(call.id)
            calls.append(
                {
                    "name": call.name,
                    "args": deepcopy(call.arguments),
                    "id": call.id,
                    "type": "tool_call",
                }
            )
        extra = {}
        if response.message and response.message.provider_items:
            extra[HISTORY_KEY] = {
                "version": 1,
                "provider": response.provider,
                "scope": self._history_scope,
                "visible": {"content": response.text, "tool_calls": deepcopy(calls)},
                "items": deepcopy(response.message.provider_items),
            }
        usage = response.usage
        inputs = usage.get("input_tokens", usage.get("prompt_eval_count"))
        outputs = usage.get("output_tokens", usage.get("eval_count"))
        usage_metadata = None
        if isinstance(inputs, int) and isinstance(outputs, int):
            usage_metadata = {
                "input_tokens": inputs,
                "output_tokens": outputs,
                "total_tokens": usage.get("total_tokens", inputs + outputs),
            }
            input_details = usage.get("input_tokens_details") or {}
            cached_tokens = input_details.get("cached_tokens")
            if isinstance(cached_tokens, int):
                usage_metadata["input_token_details"] = {"cache_read": cached_tokens}

            output_details = usage.get("output_tokens_details") or {}
            reasoning_tokens = output_details.get("reasoning_tokens")
            if isinstance(reasoning_tokens, int):
                usage_metadata["output_token_details"] = {"reasoning": reasoning_tokens}
        return AIMessage(
            content=response.text,
            tool_calls=calls,
            additional_kwargs=extra,
            response_metadata={
                **response.metadata,
                "provider": response.provider,
                "model_name": response.model,
                "usage": deepcopy(usage),
            },
            usage_metadata=usage_metadata,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        history, tools, options = self._request(messages, stop, kwargs)
        response = self.client.generate(history, tools=tools, **options)
        return ChatResult(generations=[ChatGeneration(message=self._message(response))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        history, tools, options = self._request(messages, stop, kwargs)
        emitted = ""
        with closing(self.client.stream(history, tools=tools, **options)) as events:
            for event in events:
                if event.type == "text":
                    emitted += event.text
                    yield ChatGenerationChunk(message=AIMessageChunk(content=event.text))
                elif event.type == "completed" and event.response is not None:
                    message = self._message(event.response)
                    if not event.response.text.startswith(emitted):
                        raise ProviderError("Completed text does not match the streamed text.")
                    calls = [
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": index,
                            "type": "tool_call_chunk",
                        }
                        for index, call in enumerate(message.tool_calls)
                    ]
                    # Tool calls and opaque history are released only after provider completion.
                    yield ChatGenerationChunk(
                        message=AIMessageChunk(
                            content=event.response.text[len(emitted) :],
                            tool_call_chunks=calls,
                            additional_kwargs=message.additional_kwargs,
                            response_metadata=message.response_metadata,
                            usage_metadata=message.usage_metadata,
                        )
                    )
                    return
        raise IncompleteResponse("No completed response reached the LangChain adapter.")
