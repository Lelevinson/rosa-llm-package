"""Wire adapters. Neither provider executes model-requested tools."""

import json
from collections.abc import Iterator
from copy import deepcopy
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from .auth import RESOURCE, OAuthManager
from .config import Config
from .errors import ConfigurationError, IncompleteResponse, ProviderError, UnsupportedFeature
from .http import (
    check_response,
    json_object,
    raise_provider_error,
    safe_identifier,
    strict_json_loads,
    validate_json_body,
)
from .types import Message, Response, StreamEvent, Tool, ToolCall


def sse_events(lines: Iterator[str], *, trace: dict | None = None) -> Iterator[dict]:
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                payload = "\n".join(data)
                data = []
                if payload != "[DONE]":
                    if trace is not None and trace.get("capture"):
                        trace.setdefault("raw_events", []).append(payload)
                    yield strict_json_loads(payload)
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data and "\n".join(data) != "[DONE]":
        payload = "\n".join(data)
        if trace is not None and trace.get("capture"):
            trace.setdefault("raw_events", []).append(payload)
        yield strict_json_loads(payload)


class OllamaProvider:
    def __init__(self, config: Config, http: httpx.Client):
        self.config, self.http = config, http

    def models(self) -> list[dict]:
        data = json_object(
            self.http.get(
                self.config.ollama_base_url + "/api/tags",
                follow_redirects=False,
            )
        )
        models = []
        for item in data["models"]:
            model = {
                "id": item["name"],
                "display_name": item["name"],
                "details": item.get("details", {}),
            }
            for key in ("digest", "modified_at", "size", "capabilities"):
                if key in item:
                    model[key] = item[key]
            models.append(model)
        return models

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool],
        options: dict,
        *,
        trace: dict | None = None,
    ) -> Iterator[StreamEvent]:
        unknown = set(options) - {"options", "think", "format", "keep_alive"}
        if unknown:
            raise UnsupportedFeature(
                "Unsupported Ollama option; use options, think, format or keep_alive."
            )
        if any(
            t.kind != "function" or t.namespace != "local" or t.format or t.strict is not None
            for t in tools
        ):
            raise UnsupportedFeature(
                "Ollama supports function tools without strict, custom formats or namespaces."
            )
        if len({t.name for t in tools}) != len(tools):
            raise ConfigurationError("Ollama tool names must be unique.")
        wire = []
        for message in messages:
            native_items = message.native_history("ollama")
            if native_items:
                wire.extend(native_items)
                continue
            if message.role == "developer":
                raise UnsupportedFeature(
                    "Use system messages for Ollama; developer role is unsupported."
                )
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_calls:
                if any(
                    c.kind != "function" or not isinstance(c.arguments, dict)
                    for c in message.tool_calls
                ):
                    raise UnsupportedFeature("Ollama tool calls require JSON object arguments.")
                item["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in message.tool_calls
                ]
            if message.role == "tool":
                if not message.tool_name:
                    raise ConfigurationError("Ollama tool results require tool_name.")
                item["tool_name"] = message.tool_name
            wire.append(item)
        body = {"model": self.config.ollama_model, "messages": wire, "stream": True, **options}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        text, thinking, calls, raw_calls = [], [], [], []
        validate_json_body(body)
        if trace is not None:
            if trace.get("capture"):
                trace["request"] = deepcopy(body)
            trace["_http_started"] = perf_counter()
        with self.http.stream(
            "POST",
            self.config.ollama_base_url + "/api/chat",
            json=body,
            follow_redirects=False,
        ) as stream:
            if trace is not None:
                trace["status_code"] = stream.status_code
                trace["request_id"] = safe_identifier(stream.headers.get("x-request-id"))
            if not stream.is_success:
                stream.read()
                check_response(stream)
            for line in stream.iter_lines():
                if not line:
                    continue
                if trace is not None and trace.get("capture"):
                    trace.setdefault("raw_events", []).append(line)
                chunk = strict_json_loads(line)
                if chunk.get("error"):
                    raise ProviderError("Ollama reported a generation error; check model support.")
                message = chunk.get("message", {})
                if message.get("content"):
                    text.append(message["content"])
                    yield StreamEvent("text", text=message["content"])
                if message.get("thinking"):
                    thinking.append(message["thinking"])
                for call in message.get("tool_calls", []):
                    function = call["function"]
                    args = function["arguments"]
                    if isinstance(args, str):
                        args = strict_json_loads(args)
                    if not isinstance(args, dict):
                        raise ProviderError("Ollama returned non-object tool arguments.")
                    call_id = call.get("id")
                    if call_id is None:
                        call_id = str(uuid4())
                    calls.append(ToolCall(call_id, function["name"], deepcopy(args)))
                    raw_calls.append(call)
                if chunk.get("done") is True:
                    if chunk.get("done_reason") in {"length", "error"}:
                        raise IncompleteResponse("Ollama generation did not complete normally.")
                    completed_text = "".join(text)
                    output: dict = {"role": "assistant", "content": completed_text}
                    if thinking:
                        output["thinking"] = "".join(thinking)
                    if raw_calls:
                        output["tool_calls"] = raw_calls
                    metadata = {
                        key: chunk[key]
                        for key in (
                            "total_duration",
                            "load_duration",
                            "prompt_eval_duration",
                            "eval_duration",
                            "done_reason",
                            "digest",
                        )
                        if key in chunk
                    }
                    metadata["model_identity_source"] = (
                        "provider" if "model" in chunk else "request_fallback"
                    )
                    response = Response(
                        provider="ollama",
                        model=chunk.get("model", self.config.model),
                        text=completed_text,
                        tool_calls=calls,
                        usage={
                            key: chunk[key]
                            for key in ("prompt_eval_count", "eval_count")
                            if key in chunk
                        },
                        metadata=metadata,
                        message=Message(
                            role="assistant",
                            content=completed_text,
                            tool_calls=calls,
                            provider="ollama",
                            provider_items=[output],
                        ),
                    )
                    yield StreamEvent("completed", response=response)
                    return
        raise IncompleteResponse("Ollama stream ended without done=true.")


class OpenAIProvider:
    def __init__(self, config: Config, http: httpx.Client, auth: OAuthManager):
        self.config, self.http, self.auth = config, http, auth

    def models(self) -> list[dict]:
        data = json_object(
            self.http.get(
                RESOURCE + "/models",
                follow_redirects=False,
                headers={"Authorization": "Bearer " + self.auth.access_token()},
            )
        )
        return [
            {"id": item["slug"], "display_name": item.get("display_name", item["slug"])}
            for item in data["models"]
            if item.get("visibility") == "list"
        ]

    def model(self) -> str:
        catalog = self.models()
        if not catalog:
            raise ProviderError(
                "This account has no visible models; check plan/workspace eligibility."
            )
        if self.config.openai_model:
            if self.config.openai_model not in {item["id"] for item in catalog}:
                raise ConfigurationError(
                    "OPENAI_MODEL is absent from this account's visible catalog."
                )
            return self.config.openai_model
        return catalog[0]["id"]

    @staticmethod
    def input_items(messages: list[Message]) -> list[dict]:
        items: list[dict] = []
        for message in messages:
            native_items = message.native_history("openai")
            if native_items:
                items.extend(native_items)
            elif message.role == "tool":
                if message.tool_kind not in {"function", "custom"}:
                    raise ConfigurationError("Unknown tool result kind.")
                kind = (
                    "function_call_output"
                    if message.tool_kind == "function"
                    else "custom_tool_call_output"
                )
                items.append(
                    {"type": kind, "call_id": message.tool_call_id, "output": message.content}
                )
            else:
                if message.content or not message.tool_calls:
                    # This OAuth route uses developer messages for system instructions.
                    role = "developer" if message.role == "system" else message.role
                    if role == "assistant":
                        content = [
                            {"type": "output_text", "text": message.content, "annotations": []}
                        ]
                    else:
                        content = message.content
                    items.append({"role": role, "content": content})
                for call in message.tool_calls:
                    item = {
                        "type": "function_call" if call.kind == "function" else "custom_tool_call",
                        "call_id": call.id,
                        "name": call.name,
                    }
                    if call.namespace:
                        item["namespace"] = call.namespace
                    if call.kind == "function":
                        item["arguments"] = (
                            json.dumps(call.arguments)
                            if isinstance(call.arguments, dict)
                            else call.arguments
                        )
                    else:
                        item["input"] = call.arguments
                    items.append(item)
        return items

    @staticmethod
    def wire_tools(tools: list[Tool]) -> list[dict]:
        namespaces: dict[str, list] = {}
        seen = set()
        for tool in tools:
            identity = (tool.namespace, tool.name)
            if identity in seen:
                raise ConfigurationError("Tool names must be unique within a namespace.")
            seen.add(identity)
            item = {"type": tool.kind, "name": tool.name, "description": tool.description}
            if tool.kind == "function":
                if tool.format is not None:
                    raise UnsupportedFeature("Only custom tools accept format.")
                item["parameters"] = tool.parameters
                # Do not silently rewrite caller schemas into strict schemas.
                item["strict"] = tool.strict if tool.strict is not None else False
            elif tool.format is not None:
                item["format"] = tool.format
            namespaces.setdefault(tool.namespace, []).append(item)
        return [
            {"type": "namespace", "name": name, "description": "Application tools", "tools": items}
            for name, items in namespaces.items()
        ]

    @staticmethod
    def normalize(data: dict, selected_model: str) -> Response:
        output = data["output"]
        text, calls = [], []
        for item in output:
            kind = item["type"]
            if kind == "message":
                for content in item["content"]:
                    if content["type"] == "output_text":
                        text.append(content["text"])
                    elif content["type"] == "refusal":
                        text.append(content["refusal"])
                    else:
                        raise UnsupportedFeature("Unsupported provider output content type.")
            elif kind in {"function_call", "custom_tool_call"}:
                if item.get("status", "completed") != "completed":
                    raise IncompleteResponse("OpenAI returned an unfinished tool call.")
                if kind == "custom_tool_call":
                    arguments = item["input"]
                else:
                    arguments = strict_json_loads(item["arguments"])
                if kind == "function_call" and not isinstance(arguments, dict):
                    raise ProviderError("OpenAI returned non-object function arguments.")
                calls.append(
                    ToolCall(
                        id=item["call_id"],
                        name=item["name"],
                        arguments=arguments,
                        kind="custom" if kind == "custom_tool_call" else "function",
                        namespace=item.get("namespace"),
                    )
                )
            elif kind != "reasoning":
                raise UnsupportedFeature("Unsupported provider output item type.")
        completed_text = "".join(text)
        return Response(
            provider="openai",
            model=data.get("model", selected_model),
            text=completed_text,
            tool_calls=calls,
            usage=data.get("usage") or {},
            metadata={
                "id": data.get("id"),
                "status": data.get("status"),
                "model_identity_source": "provider" if "model" in data else "request_fallback",
            },
            message=Message(
                role="assistant",
                content=completed_text,
                tool_calls=calls,
                provider="openai",
                provider_items=output,
            ),
        )

    def stream(
        self,
        messages: list[Message],
        tools: list[Tool],
        options: dict,
        *,
        trace: dict | None = None,
    ) -> Iterator[StreamEvent]:
        if set(options) - {"tool_choice", "parallel_tool_calls"}:
            raise UnsupportedFeature(
                "This OAuth adapter accepts tool_choice and parallel_tool_calls only; "
                "other API options are not implemented or are blocked by ChatGPT plan access."
            )
        if "parallel_tool_calls" in options and not isinstance(
            options["parallel_tool_calls"], bool
        ):
            raise ConfigurationError("parallel_tool_calls must be a boolean.")
        if "tool_choice" in options and options["tool_choice"] not in ("auto", "none", "required"):
            raise UnsupportedFeature(
                "This OAuth namespace path supports auto, none or required tool_choice only."
            )
        # Validate caller input before performing authenticated network requests.
        items, wire_tools = self.input_items(messages), self.wire_tools(tools)
        if trace is not None:
            trace["phase"] = "model_selection"
        model = self.model()
        body: dict = {"model": model, "input": items, "store": False, "stream": True, **options}
        if wire_tools:
            body["tools"] = wire_tools
        headers = {
            "Authorization": "Bearer " + self.auth.access_token(),
            "Accept": "text/event-stream",
        }
        completed_items: dict[int, dict] = {}
        validate_json_body(body)
        if trace is not None:
            if trace.get("capture"):
                trace["request"] = deepcopy(body)
            trace["_http_started"] = perf_counter()
            trace["phase"] = "inference"
        with self.http.stream(
            "POST",
            RESOURCE + "/responses",
            json=body,
            headers=headers,
            follow_redirects=False,
        ) as stream:
            if trace is not None:
                trace["status_code"] = stream.status_code
                trace["request_id"] = safe_identifier(stream.headers.get("x-request-id"))
            if not stream.is_success:
                stream.read()
                check_response(stream)
            # A live OAuth response omitted this header despite carrying valid SSE.
            # Missing MIME metadata is allowed; parsed events and terminal status
            # still determine success. Explicitly incompatible MIME types fail.
            content_type = stream.headers.get("content-type")
            if (
                content_type
                and content_type.split(";", 1)[0].strip().lower() != "text/event-stream"
            ):
                raise ProviderError("OpenAI returned an unexpected stream content type.")
            for event in sse_events(stream.iter_lines(), trace=trace):
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    yield StreamEvent("text", text=event["delta"])
                elif kind == "response.output_item.done":
                    index = event["output_index"]
                    if not isinstance(index, int) or index < 0:
                        raise ProviderError("OpenAI returned an invalid output item index.")
                    completed_items[index] = event["item"]
                elif kind in {"response.failed", "error"}:
                    error = event.get("response", {}).get("error") or event.get("error") or event
                    raise_provider_error(
                        error.get("code"), request_id=stream.headers.get("x-request-id")
                    )
                elif kind == "response.incomplete":
                    raise IncompleteResponse(
                        "OpenAI returned response.incomplete; partial text is not success."
                    )
                elif kind == "response.completed":
                    data = event["response"]
                    if data.get("status") != "completed":
                        raise IncompleteResponse(
                            "OpenAI completion event has a non-completed status."
                        )
                    if not data.get("output") and completed_items:
                        # Some live streams omit output from the terminal snapshot.
                        # Only fully completed items are replayable as history.
                        data = {
                            **data,
                            "output": [completed_items[i] for i in sorted(completed_items)],
                        }
                    elif completed_items:
                        # A nonempty terminal snapshot must not omit or change a tool
                        # that was already completed. Fail instead of silently losing calls.
                        for index, item in completed_items.items():
                            if index >= len(data["output"]):
                                raise ProviderError(
                                    "OpenAI terminal snapshot omitted a completed item."
                                )
                            final_item = data["output"][index]
                            keys = ("type", "id")
                            if item["type"] in {"function_call", "custom_tool_call"}:
                                keys += ("call_id", "name", "namespace", "arguments", "input")
                            if any(final_item.get(key) != item.get(key) for key in keys):
                                raise ProviderError(
                                    "OpenAI terminal snapshot changed a completed item."
                                )
                    response = self.normalize(data, model)
                    yield StreamEvent("completed", response=response)
                    return
        raise IncompleteResponse("OpenAI stream ended without response.completed.")
