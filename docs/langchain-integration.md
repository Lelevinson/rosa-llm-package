# ROSA / LangChain integration

Current package hardening and verification are described in
[hardening-report.md](hardening-report.md); the integration evidence below is historical.

Initial ROSA integration verified on 2026-10-22; general adapter audit updated on 2026-10-23.
See the [current general compatibility report](langchain-compatibility.md) for provider capabilities and boundaries. The adapter is
`rosa_llm.langchain.ChatRosaLLM`. It belongs to this package; ROSA's source is unchanged.

The base package and adapter require Python >=3.11; development remains on
Python 3.12 for the installed ROS 2 Jazzy binaries. This lower package minimum
does not resolve uv dependency resolution across ROSA Research's declared
Python >=3.9 range: a consumer still needs an explicit compatible Python range
or dependency marker. ROSA Research's default groups and its installed Jazzy
runtime impose additional requirements. No permanent integration is implied.
See [Python compatibility](python-compatibility.md) for current verification.

## Responsibility boundary

```text
ROSA / LangChain AgentExecutor
    -> ChatRosaLLM (LangChain messages, tool definitions, response/chunk conversion)
        -> rosa_llm.Client (shared generate/stream API)
            -> OpenAI OAuth or Ollama provider
```

The adapter does not execute tools. LangChain's executor validates tool arguments
and invokes the registered ROSA tool. The adapter preserves returned arguments
without repairing types, filling defaults, or adding retries. An optional
`allowed_tools` set limits the offered function definitions and rejects model
requests for other names before they reach the executor.

The base package still has no LangChain or ROSA runtime dependency. Its
`langchain` extra adds `langchain-core==0.3.86`. The optional development group
`integration` installs `jpl-rosa==1.0.10` and `langchain==0.3.30` for the tests and
live example. This is a deliberately pinned compatibility target, not a claim
of support for LangChain 1.x.

## Source inspection

The installed source was inspected, rather than assuming compatibility from
class names. The reference ROSA environment uses ROSA 1.0.10, LangChain 0.3.30,
and langchain-core 0.3.86. The integration environment uses those same versions.
Pydantic resolved to 2.14.1 in this project's integration environment (the earlier
ROSA environment has 2.13.5).

Paths below are relative to `.venv/lib/python3.12/site-packages/` in this project:

| Code inspected | Relevant contract and adapter decision |
| --- | --- |
| `rosa/rosa.py`: `ROSA.__init__`, `_get_agent`, `_get_executor`, `invoke`, `astream` | Accept a `BaseChatModel`, call `with_config`, build a tool-calling agent, and consume sync or async executor output. The model passed by the startup script changes; the ROSA class does not. |
| `langchain/agents/tool_calling_agent/base.py`: `create_tool_calling_agent` | Calls `llm.bind_tools(tools)` and composes prompt, model and parser. Adapter binding returns a separate Runnable with converted tools. |
| `langchain_core/runnables/base.py`: `RunnableBinding.__getattr__` | Checked delegation of `bind_tools` through ROSA's `with_config` wrapper. Actual ROSA construction and execution are covered by tests. |
| `langchain_core/language_models/chat_models.py`: `BaseChatModel`, `_generate_with_cache`, `_agenerate_with_cache`, `_should_stream`, `stream`, `_agenerate`, `_astream` | Implement `_generate`, `_stream`, `_llm_type`. Reuse the inspected async worker-thread bridge. The base class owns callback emission; the adapter avoids duplicate callbacks. Global caching is disabled by default for account-selected models. |
| `langchain_core/language_models/_utils.py`: `_normalize_messages` | Checked message normalization on the call path. Opaque provider history in `additional_kwargs` survives text-message handling. Non-text inputs are rejected by the adapter. |
| `langchain_core/utils/function_calling.py`: `convert_to_openai_tool` | Use LangChain's existing conversion for callables, Pydantic classes and BaseTools. Titles may be removed by this upstream converter; returned model arguments are not altered. |
| `langchain/agents/output_parsers/tools.py`: `parse_ai_message_to_tool_action` | Expects `AIMessage.tool_calls` with name, dictionary args and call ID. The adapter returns that shape with unqualified ROSA tool names. |
| `langchain/agents/format_scratchpad/tools.py`: `format_to_tool_messages`, `_create_tool_message` | Retains the original AIMessage, then adds a ToolMessage whose name may be in `additional_kwargs`. Preserve opaque reasoning/output items on that original message and recover result names from the message or matching call ID. |
| `langchain_core/messages/ai.py` and `outputs/chat_generation.py` | Checked chunk merging and conversion back to a complete generation. Text deltas are emitted once; tool-call chunks, usage and opaque history are emitted only after provider completion. |
| `langchain_core/tracers/context.py`, `tracers/run_collector.py` | `collect_runs()` records actual executor/tool runs locally for the live check. It does not require a tracing service. Tool input is retained in the collector's original string representation. |
| `rosa/tools/ros2.py`: `ros2_node_list`, `get_entities`, `execute_ros_command` | The real tool executes `ros2 node list`. A direct CLI comparison catches tool results containing command errors, which ROSA can otherwise package as strings in a list. |
| `rosa/rosa.py`: `_record_chat_history`, `_token_callback` | ROSA stores final user/assistant text between user turns and only enables its built-in token display for ChatOpenAI/AzureChatOpenAI instances. These existing behaviors are unchanged. |

The inspected ROSA checkout's `src/rosa/rosa.py` and the installed distribution's
`rosa/rosa.py` had the same SHA-256:
`21c35b1bdb14f6c8f6fd2f449943de7f1ba177fdce51ba0a6344b5ec9991e66b`.
No ROSA methods, tools or executor instances were patched for the integration.

Upstream references:
[versioned BaseChatModel source](https://github.com/langchain-ai/langchain/blob/langchain-core%3D%3D0.3.86/libs/core/langchain_core/language_models/chat_models.py),
[ROSA repository](https://github.com/nasa-jpl/rosa).
The installed files above are the source of truth for the tested dependency set.

## Install and use

In this project:

```bash
uv sync --locked --extra langchain --group integration
```

`--extra langchain` enables this package's optional adapter dependency;
`--group integration` also installs the development-only ROSA test dependencies.
For a uv project that already installs ROSA and declares a Python range
compatible with >=3.11, add the adapter with:

```bash
uv add "rosa-llm[langchain] @ git+https://github.com/Lelevinson/rosa-llm-package.git@v0.2.0"
```

This command requires the approved `v0.2.0` tag on the public GitHub repository.
Public HTTPS reads do not require GitHub sign-in.
uv records the resolved commit in the consumer lockfile. Pin the full commit SHA
when an immutable source reference is required. A declared Python >=3.9 range
still requires a separate compatible-range or conditional-marker decision.
`uv add --editable /path/to/rosa-llm-package --extra langchain` is an alternative
for local development; it should not replace the shared Git dependency. These
commands change the consuming project only when explicitly run there.

Set `LLM_PROVIDER=openai` in the calling project's `.env` to use the saved ChatGPT
login, or select a reachable Ollama endpoint/model. Your existing startup code
then supplies the adapter using ROSA's existing `llm` argument:

```python
from rosa.rosa import ROSA
from rosa_llm import create_client
from rosa_llm.langchain import ChatRosaLLM

with create_client() as client:
    model = ChatRosaLLM(client=client, allowed_tools={"ros2_node_list"})
    agent = ROSA(ros_version=2, llm=model, streaming=False, max_iterations=3)
    answer = agent.invoke("Use ros2_node_list to list all ROS 2 nodes.")
    print(answer)
```

The `with` block closes the shared HTTP client after the agent finishes. Keep it
open for the lifetime of a conversational agent. Omitting `allowed_tools` exposes
all functions ROSA binds; the example deliberately permits only node listing.
Provider and model selection remain `.env` concerns.

## Reproduce the checks

```bash
source /opt/ros/jazzy/setup.bash
uv run --extra langchain --group integration pytest -q
uv run --extra langchain --group integration python examples/rosa_readonly.py --provider openai
uv run --extra langchain --group integration python examples/rosa_readonly.py --provider openai --stream
```

`source` loads ROS Jazzy's paths into the current shell. `--provider openai`
overrides `.env` for the example; `--stream` selects ROSA's asynchronous streaming
entry point. Both live commands consume account usage and require the saved login.
An empty ROS graph is valid; they do not start a node themselves.

The live example exposes only `ros2_node_list`, requires exactly one completed
tool run, rejects empty/error answers, and compares the actual observation to
direct CLI output before and after the request. It reports inconclusive/failure
if the graph changes during that comparison. It uses ROSA's normal constructor
and normal `invoke`/`astream` methods.

The initial 2026-10-22 automated command passed 130 tests (107 standalone, 21 adapter and two
actual ROSA executor tests with mocked HTTP). Ruff checks, formatting, lock
validation and wheel/source builds passed. Isolated wheel checks confirmed that
the base package needs no LangChain, while the optional wheel extra constructs
the adapter with the pinned core version. The concurrency test uses fresh spawned
processes, avoiding a fork of LangChain's already-started worker threads.

During validation a temporary node named `/rosa_llm_adapter_probe_20627` was
registered with no publishers or subscriptions, making a nonempty result
verifiable. Both modes discovered that node; their actual tool observation and
final answer matched the direct CLI result. The node was removed afterward.
It did not publish messages or issue robot-control commands.

## Compatibility limits

- Tested: OpenAI `gpt-6.1-sol` with actual ROSA node listing in both modes; Ollama
  adapter message/function round trips with mocked HTTP. Later generic live
  Ollama checks and their model-specific results are in the
  [compatibility report](langchain-compatibility.md). ROSA with live Ollama
  remains unverified by this package's checks.
- The adapter supports text and JSON function tools. OpenAI strict schemas and
  auto/none/required choice, its standard function-calling structured-output wrapper,
  and Ollama stop sequences were added in the general audit. Named choice,
  hosted/custom text tools and multimodal messages remain unsupported. See the
  [current capability table](langchain-compatibility.md#provider-capabilities-and-boundaries).
- Provider HTTP streaming and ROSA's presentation mode are separate. OpenAI
  always uses its required streamed HTTP protocol; ROSA `invoke` receives a
  completed message, while `astream` can emit text and executor/tool events.
- Async uses worker threads, not a new native async provider. An in-flight HTTP
  operation may continue until its configured timeout after task cancellation.
- ROSA's existing built-in token display does not recognize this new model
  class. Usage is available in LangChain message metadata. ROSA's existing
  between-turn history stores final text; the adapter preserves full provider
  items within the current agent/tool loop but does not rewrite ROSA's history policy.
- The local collector currently emits an upstream Pydantic deprecation warning
  (`run.copy()`); this does not affect tool execution. No upstream file was edited.
- These checks establish the tested integration path. They do not establish
  arbitrary robot-task accuracy or compatibility with every LangChain version.
