"""Live generic tool check, with actual LangChain validation and execution.

uv run --extra langchain --group integration python examples/langchain_tools.py --provider openai
No ROSA, ROS, robot, or remote tools are used. Each mode makes at most three model requests.
"""

import argparse
import asyncio
import json
from dataclasses import replace

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from pydantic import BaseModel

from rosa_llm import Config, LLMError, create_client
from rosa_llm.langchain import ChatRosaLLM


class Sample(BaseModel):
    label: str
    values: list[float]


class SummarizeInput(BaseModel):
    """Summarize nested samples with an explicit nullable note."""

    samples: list[Sample]
    enabled: bool
    note: str | None


async def async_result(executor, prompt):
    result = None
    async for event in executor.astream_events({"input": prompt}, version="v2"):
        if event["event"] == "on_chain_end" and event["name"] == "AgentExecutor":
            result = event["data"]["output"]
    if result is None:
        raise RuntimeError("The asynchronous executor did not finish.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["openai", "ollama"], required=True)
    parser.add_argument("--async-stream", action="store_true")
    parser.add_argument("--strict", action="store_true", help="request OpenAI strict schemas")
    args = parser.parse_args()
    observations = []
    tool_inputs = []

    @tool(args_schema=SummarizeInput)
    def summarize(samples, enabled, note):
        """Return labels, numeric totals, enabled and note from nested samples."""
        output = {
            "labels": [s.label for s in samples],
            "totals": [sum(s.values) for s in samples],
            "enabled": enabled,
            "note": note,
        }
        observations.append(output)
        return json.dumps(output, ensure_ascii=False)

    expected = {"labels": ["台灣"], "totals": [2.0], "enabled": False, "note": None}
    prompt = (
        'Call summarize exactly once with samples=[{"label":"台灣","values":[0,-1.5,3.5]}], '
        "enabled=false, note=null. After the tool result, report the result briefly."
    )
    config = replace(Config.load(), provider=args.provider)
    try:
        with create_client(config) as client:
            model = ChatRosaLLM(client=client)
            if args.strict:
                # The agent itself still receives an ordinary model and ordinary tool.
                # Use the standard converted schema on the tool, not a name-specific adapter path.
                from langchain_core.utils.function_calling import convert_to_openai_tool

                schema = convert_to_openai_tool(summarize, strict=True)
                # The direct loop tests forced choice, strict schemas and actual ToolMessage output.
                bound = model.bind_tools(
                    [schema], tool_choice="required", parallel_tool_calls=False
                )
                first = bound.invoke(prompt)
                tool_inputs = [call["args"] for call in first.tool_calls]
                if len(first.tool_calls) != 1 or first.tool_calls[0]["name"] != summarize.name:
                    raise RuntimeError("Expected one summarize function call.")
                result_message = summarize.invoke(first.tool_calls[0])
                final = model.bind_tools([schema], tool_choice="none").invoke(
                    [
                        ("human", prompt),
                        first,
                        result_message,
                    ]
                )
                if final.tool_calls:
                    raise RuntimeError("The provider ignored tool_choice=none.")
                output = final.content
                models = [
                    first.response_metadata["model_name"],
                    final.response_metadata["model_name"],
                ]
            else:
                prompt_template = ChatPromptTemplate.from_messages(
                    [
                        ("system", "Use the provided tools exactly as requested."),
                        ("human", "{input}"),
                        MessagesPlaceholder("agent_scratchpad"),
                    ]
                )
                executor = AgentExecutor(
                    agent=create_tool_calling_agent(model, [summarize], prompt_template),
                    tools=[summarize],
                    max_iterations=3,
                    return_intermediate_steps=True,
                )
                result = (
                    asyncio.run(async_result(executor, prompt))
                    if args.async_stream
                    else executor.invoke({"input": prompt})
                )
                output = result["output"]
                tool_inputs = [action.tool_input for action, _ in result["intermediate_steps"]]
                models = [
                    action.message_log[0].response_metadata["model_name"]
                    for action, _ in result["intermediate_steps"]
                ]
            if observations != [expected] or not isinstance(output, str) or not output.strip():
                raise RuntimeError(
                    "Actual tool observations did not match the requested nested values."
                )
            print(
                json.dumps(
                    {
                        "verified": True,
                        "provider": config.provider,
                        "models": models,
                        "mode": "strict_direct"
                        if args.strict
                        else "async_agent"
                        if args.async_stream
                        else "sync_agent",
                        "observations": observations,
                        "tool_inputs": tool_inputs,
                        "response": output,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    except (LLMError, RuntimeError) as error:
        print(
            json.dumps(
                {
                    "verified": False,
                    "error": str(error),
                    "observations": observations,
                    "tool_inputs": tool_inputs,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
