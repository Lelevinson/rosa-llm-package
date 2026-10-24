"""One explicit, harmless tool round trip; no automatic execution of arbitrary calls."""

from rosa_llm import Message, Tool, create_client

echo = Tool(
    "echo",
    "Return the value unchanged.",
    {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)
history = [Message("user", "Use echo with value hello, then explain its result briefly.")]
with create_client() as client:
    first = client.generate(history, tools=[echo])
    if len(first.tool_calls) != 1:
        raise RuntimeError("Expected exactly one echo call; nothing executed.")
    call = first.tool_calls[0]
    if (
        call.name != "echo"
        or call.kind != "function"
        or call.namespace not in {None, "local"}
        or not isinstance(call.arguments, dict)
        or set(call.arguments) != {"value"}
        or not isinstance(call.arguments["value"], str)
    ):
        raise RuntimeError("Unexpected tool request; nothing executed.")
    result = call.arguments["value"]
    history += [first.message, Message("tool", result, tool_call_id=call.id, tool_name=call.name)]
    print(client.generate(history, tools=[echo]).text)
