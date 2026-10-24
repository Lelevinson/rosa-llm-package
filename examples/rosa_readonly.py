"""Explicit live ROSA check: offer only ros2_node_list and verify its actual result.

Run from the project root, with ROS Jazzy sourced:
uv run --extra langchain --group integration python examples/rosa_readonly.py --provider openai
Add --stream to exercise ROSA.astream instead of ROSA.invoke.
"""

import argparse
import asyncio
import json
import subprocess
import sys
from dataclasses import replace

from langchain_core.tracers.context import collect_runs
from rosa.rosa import ROSA

from rosa_llm import Config, LLMError, create_client
from rosa_llm.langchain import ChatRosaLLM

PROMPT = (
    "Call ros2_node_list exactly once to list all current ROS 2 nodes. "
    "Use pattern=null and blacklist=[] so no nodes are filtered. "
    "Then report exactly the nodes returned by that tool. Use no other tools."
)


def direct_nodes() -> list[str]:
    result = subprocess.run(["ros2", "node", "list"], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError("The direct ros2 node list command failed; check the ROS environment.")
    nodes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if any(not node.startswith("/") for node in nodes):
        raise RuntimeError("The direct node-list output contained a non-node line.")
    return nodes


def tool_records(roots) -> list[dict]:
    pending, found, seen = list(roots), [], set()
    while pending:
        run = pending.pop()
        if run.id in seen:
            continue
        seen.add(run.id)
        pending.extend(run.child_runs)
        if run.run_type == "tool":
            found.append(
                {
                    "name": run.name,
                    "input": run.inputs,
                    "output": run.outputs,
                    "completed": run.end_time is not None,
                    "error": run.error,
                }
            )
    return found


async def streamed(agent):
    events = [event async for event in agent.astream(PROMPT)]
    if any(event["type"] == "error" for event in events):
        raise RuntimeError("ROSA emitted an error event; this is not a successful execution.")
    finals = [event["content"] for event in events if event["type"] == "final"]
    if len(finals) != 1:
        raise RuntimeError("ROSA did not emit exactly one final event.")
    return finals[0], [event["type"] for event in events]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["ollama", "openai"])
    parser.add_argument("--stream", action="store_true", help="exercise ROSA's async streaming API")
    args = parser.parse_args()
    config = Config.load()
    if args.provider:
        config = replace(config, provider=args.provider)
    try:
        before = direct_nodes()
        with create_client(config) as client:
            model = ChatRosaLLM(client=client, allowed_tools={"ros2_node_list"})
            agent = ROSA(
                ros_version=2,
                llm=model,
                streaming=args.stream,
                verbose=False,
                max_iterations=3,
                accumulate_chat_history=False,
            )
            with collect_runs() as collector:
                if args.stream:
                    output, events = asyncio.run(streamed(agent))
                else:
                    output, events = agent.invoke(PROMPT), []
        records = tool_records(collector.traced_runs)
        after = direct_nodes()
        report = {
            "provider": config.provider,
            "mode": "astream" if args.stream else "invoke",
            "direct_nodes_before": before,
            "direct_nodes_after": after,
            "tool_runs": records,
            "response": output,
            "events": events,
        }
        if not output.strip() or output.startswith("An error occurred:"):
            raise RuntimeError("ROSA returned an empty answer or an error string.")
        if len(records) != 1 or records[0]["name"] != "ros2_node_list":
            raise RuntimeError("Expected exactly one actual ros2_node_list tool execution.")
        run = records[0]
        if not run["completed"] or run["error"]:
            raise RuntimeError("The read-only tool did not finish successfully.")
        observation = run["output"].get("output", run["output"])
        if not isinstance(observation, dict) or not isinstance(observation.get("nodes"), list):
            raise RuntimeError("The actual tool did not return a node list.")
        if sorted(before) != sorted(after):
            raise RuntimeError(
                "The ROS graph changed during the check; comparison is inconclusive."
            )
        if sorted(observation["nodes"]) != sorted(before):
            raise RuntimeError("The actual tool result differs from direct ROS CLI output.")
        # This proves tool execution/result agreement, not the correctness of arbitrary robot tasks.
        report["verified"] = True
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0
    except (LLMError, OSError, subprocess.TimeoutExpired, RuntimeError) as error:
        message = (
            str(error) if isinstance(error, (LLMError, RuntimeError)) else type(error).__name__
        )
        print(json.dumps({"verified": False, "error": message}))
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
