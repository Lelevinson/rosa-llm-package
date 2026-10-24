# LangChain compatibility audit and validation

For current 0.2.0 corrections, request recording and verification, see
[hardening-report.md](hardening-report.md). This report retains its original audit evidence.

Date: 2026-10-23. Package: `rosa-llm` 0.1.0. Adapter:
`rosa_llm.langchain.ChatRosaLLM`. This audit covers general infrastructure;
there are no ROSA, JTVR, tool-name or experimental-scenario branches in the adapter.

## Contract

Ordinary LangChain tools can be reused without adapter-specific changes when
their converted definition is a JSON function tool with object arguments and a
unique name containing 1–64 letters, digits, underscores or dashes. Tools remain
responsible for their own validation and execution through LangChain. The adapter
does not run functions, validate arguments against their schemas, repair values,
parse nested JSON-looking strings, fill defaults, or retry model mistakes.

Supported definitions use LangChain's `convert_to_openai_tool` and
`convert_to_openai_function`: decorated tools, `BaseTool`, `StructuredTool`,
callables, Pydantic v1/v2 models, TypedDict, JSON Schema, wrapped/flat OpenAI
function dictionaries, and the Anthropic/Bedrock dictionary forms understood by
the pinned converter. Pydantic/TypedDict schema classes describe functions;
they are not executable tools by themselves. A tool must still be registered
with the agent or invoked by the application.

The model accepts text `HumanMessage`, `SystemMessage`, `AIMessage`,
`ToolMessage`, supported `ChatMessage` roles, and LangChain's usual input
conversions such as role/content tuples. Text blocks are flattened in order.
Modern `tool_calls` with dictionary arguments and nonempty call IDs are the
execution interface. Tool results correlate by ID; Ollama additionally needs a
name, taken from the result or the earlier assistant call. Conflicting names and
duplicate IDs in a supplied history are errors. ToolMessage artifacts and status
are application metadata: the provider receives its text content, not the artifact.

`invoke`, `stream`, `ainvoke`, `astream`, `batch`, `abatch`, Runnable prompt
composition, callbacks, event streams and ordinary tool-calling AgentExecutor
loops are exercised. Async uses the inspected LangChain worker-thread bridge,
not native asynchronous HTTP. The caller owns and closes `Client` after all
requests finish. Cancellation cannot interrupt a blocking HTTP read immediately;
the configured transport timeout still applies.

## Defects and changes

| Boundary | Finding and resolution |
| --- | --- |
| Tool binding | Bindings contained package dataclasses and discarded/rejected strict information. Bindings now contain standard serializable LangChain function dictionaries. Explicit strict conversion uses LangChain's converter on copies; `Tool.strict` carries the result to OpenAI. Unsupported function/wrapper fields raise errors rather than disappearing. |
| Names | A Python-identifier-like rule rejected digit-leading tool names. The shared value object now accepts the documented package name alphabet; a real empty-input `BaseTool` named `0-health-check` executes in the tests. |
| Tool result correlation | OpenAI results unnecessarily required a name. OpenAI now accepts call ID alone; Ollama still requires a resolvable name. Conflicts and duplicate history IDs fail explicitly. |
| Ollama call identity | Synthesized IDs restarted at `ollama_0` each response. Missing native IDs now receive UUIDs, avoiding collisions across turns. Native IDs are retained when present. |
| Arguments | Ollama accepted only native argument objects. A JSON-string object envelope is now decoded once; nested strings and legitimate fields remain unchanged. No upstream argument-repair heuristics were copied. Malformed/non-object envelopes still fail. |
| Opaque continuation | Replaying saved provider items could silently override edited AIMessage fields. Versioned state now records a snapshot and a configuration scope. Unchanged state retains reasoning/native items; edits use current public content/calls and discard the stale snapshot. Foreign/unversioned state is rejected. |
| Execution policy | All unoffered names were rejected before the agent could handle them. By default, unknown function names now reach LangChain's standard InvalidTool handling. Explicit `allowed_tools` still filters offerings and rejects disallowed model calls. Plain chat works on a restricted model without binding tools. |
| Request controls | OpenAI now supports automatic/none/required choice and the parallel-call flag; Ollama accepts LangChain stop through native `options.stop`, with duplicate declarations rejected. Unsupported controls are explicit errors. |
| Structured output | The pinned base wrapper silently ignores some options. The adapter permits its standard function-calling parser path and rejects unsupported method/strict keywords rather than ignoring them. |
| Resource lifetime | Client and adapter generators explicitly close their child generators on completion, error or close. Public sync and async stream-close tests verify HTTP-response release. |
| Metadata/cache | Input/output/total usage is normalized, including OpenAI cache-read and reasoning details. Raw usage and provider metadata remain available. Explicit caches are partitioned per model instance; global caching stays disabled by default. |

Saved opaque state is scoped to provider, configured model, endpoint, credential
directory and profile. This is configuration isolation, not authentication or a
cryptographic integrity guarantee. It survives message serialization and model
recreation with the same configuration. It is not a portable cross-provider
history format. Pre-audit saved state must be discarded or rebuilt from its
public message fields; remove `additional_kwargs["rosa_llm_history"]` explicitly.
Replaying reasoning after editing a message is intentionally avoided; a model
that requires its old reasoning may reject a transformed history.

## Provider capabilities and boundaries

| Capability | ChatGPT-plan OAuth | Ollama |
| --- | --- | --- |
| Text, modern function calls/results, multiple calls | Supported; generic live checks passed | Generic live sync/async checks passed on GPT-OSS; Qwen nullable-value failure recorded below |
| Text streaming | Native SSE; requires completed terminal event | Native NDJSON; requires `done=true` |
| Tool streaming | Complete validated calls released only after successful completion | Same; no partial argument fragments |
| Stop sequences | Unsupported by Responses API | Forwarded through native `options.stop` |
| Strict function schemas | Explicit `strict=True/False`; provider's schema subset still applies | Explicit strict option rejected; native chat API has no such field |
| Tool choice | None/default, `auto`, `none`, `required`; LangChain `any`/True map to required, False to default | Default/`auto`; forced choice rejected because native chat exposes no selector |
| Named forced choice | Rejected locally: live API rejected bare and qualified names with the current namespace envelope | Unsupported |
| Parallel-call control | Boolean forwarded; live strict check exercised False | No request flag exposed; model may still return multiple calls |
| `with_structured_output` | Standard function-calling parser, including `include_raw`; mock tested | Wrapper needs required choice, so rejected; native `format` can still be supplied separately |
| Provider options | Only `tool_choice` and `parallel_tool_calls` implemented | `options`, `think`, `format`, `keep_alive` forwarded |
| System/developer | System maps to developer on this route | System supported; developer rejected |

Named choice is a limitation of this adapter's current OAuth namespace path,
not a claim that the general OpenAI Responses API lacks named function choice.
The first live strict/forced request returned HTTP 400, `param=tool_choice`:
`Tool choice 'summarize' not found in 'tools' parameter.` A minimized request
failed for both `echo_control` and `local.echo_control`. Strict alone and required
alone succeeded; the final strict round trip used required and parallel=False.
These failures are retained as negative evidence, not converted into successes.

Other deliberate adapter boundaries: text only; no legacy `function_call` /
`FunctionMessage` protocol, nonlocal/custom text tools, hosted tools, images,
audio, token counting, native async HTTP, or reconstruction of a live Client from
serialized model configuration. Model output with malformed JSON or non-object
function arguments raises ProviderError. It is not converted to
`AIMessage.invalid_tool_calls`; automated agent recovery from malformed argument
JSON is outside this contract. This avoids LangChain 0.3 chunk parsing repairing
incomplete JSON into an executable call. Incomplete streams never release tool
calls; empty completed responses without text or calls are errors.

Extra OpenAI options are not all provider restrictions. For example, reasoning
controls are not implemented by this client. Conversely, ChatGPT-plan preview
restrictions block sampling fields such as temperature/top_p and require
streaming, `store=false`, and explicit input context. See the
[official preview restrictions](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
General API tool controls are described in
[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling).
Ollama's native request fields are documented in
[Ollama chat](https://docs.ollama.com/api/chat).

## Inspected implementation and evidence

Test environment: Python 3.12.3, uv 0.13.2, langchain-core **0.3.86**,
LangChain **0.3.30**, langchain-openai **0.3.35**, Pydantic **2.14.1**.
Compatibility is pinned to this set; LangChain 1.x is not claimed.
Installed source, rather than current online LangChain documentation, was the
contract reference:

- `langchain_core/language_models/chat_models.py`: BaseChatModel generation,
  streaming, worker-thread async bridge, cache and structured-output wrapper.
- `langchain_core/utils/function_calling.py`: tool/schema conversion, strict
  conversion, injected argument exclusion, and legacy string-tool `__arg1`.
- `langchain_core/messages/{ai,tool}.py`: calls, invalid calls, ToolMessage,
  chunk assembly and partial-JSON behavior.
- `langchain/agents/{tool_calling_agent/base,output_parsers/tools,format_scratchpad/tools}.py`:
  model binding, argument handoff, legacy string unpacking, result formatting and
  preservation of the assistant message log.
- `langchain_openai/chat_models/base.py`: binding and message conversion.
  Automated differential checks compare converted schemas with ChatOpenAI.
- `langchain_ollama/chat_models.py` in the existing sibling rosa-test environment:
  native message conversion, UUID call IDs, string-envelope parsing and argument
  repair rules. The latter were deliberately not adopted.

Automated evidence uses actual LangChain objects and execution loops with mocked
provider HTTP, not live model responses. It covers both providers, nested models,
enums, nullable/default/empty inputs, Unicode, arrays, dicts, bool/zero/empty-string
values, TypedDict, Pydantic v1/v2, BaseTool, StructuredTool coroutine execution,
legacy string Tool, injected IDs, artifacts, handled ToolException, return_direct,
unknown tools, reversed result order, repeated turns, serialized/edited histories,
stream aggregation/replay, callbacks/events, sync/async batch, transport closure,
option rejection and malformed argument failures. Existing OAuth and actual ROSA
executor regression tests remain included; ROSA source was not modified.

Live evidence on 2026-10-23: `gpt-6.1-sol` passed the sync generic AgentExecutor,
the async AgentExecutor/event stream, and a strict function/result loop with
required choice, parallel=False and final choice=none. Each executed the real
LangChain `summarize` tool exactly once. Its independently checked observation was
`{"labels":["台灣"],"totals":[2.0],"enabled":false,"note":null}`.
Ollama follow-up on 2026-10-23: localhost remained unreachable, but the route-derived
WSL gateway (machine-specific address omitted) served `gpt-oss:20b` and `qwen3.5:9b`.
Tests used process-environment overrides; `.env` was not edited. Its existing
`qwen3:8b` model is absent from the returned catalog.

- `gpt-oss:20b`: both generic synchronous AgentExecutor and asynchronous
  AgentExecutor/event-stream checks passed. Each executed the actual tool exactly
  once with inputs and observations matching the expected nested values above.
- `qwen3.5:9b`: sync and async runs failed the exact-value success criterion.
  The first sync run did not capture detailed inputs. The async run executed the
  tool once with `note="None"` (a string), instead of requested JSON null. Its
  other observed values matched. This value is allowed by `str | None`, so
  Pydantic did not reject it; schema validity alone does not establish task success.
  The adapter does not repair this value or count the run as a success.

Live checks demonstrate tool transport/execution for these runs, not reliability
across prompts or models. Strict schemas and forced choice remain unsupported by
Ollama's native chat interface as documented in the table.

Final verification: **212 automated tests passed**, Ruff lint/format and uv lock
checks passed, and wheel/source builds passed. The final wheel was installed in
two disposable uv environments: the base imported without LangChain installed;
the extra completed a real LangChain tool round trip using mocked native HTTP.
One upstream RunCollector Pydantic deprecation warning remains. Detailed results
are in [validation results](validation.md#2026-10-23-general-adapter-audit).

## Reproduce

```bash
uv run --locked --extra langchain --group integration pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
uv build
uv run --locked --extra langchain --group integration python examples/langchain_tools.py --provider openai
uv run --locked --extra langchain --group integration python examples/langchain_tools.py --provider openai --async-stream
uv run --locked --extra langchain --group integration python examples/langchain_tools.py --provider openai --strict
```

The integration dependency group supplies the AgentExecutor used by the example.
`--async-stream` selects its asynchronous event stream. `--strict` selects a
direct two-request tool/result loop using strict schemas and the real tool.
The live commands consume account usage and use the existing saved OAuth login.
Source `/opt/ros/jazzy/setup.bash` first to include the two optional ROSA tests;
the generic contract tests need no ROS installation. Ruff checks formatting and
lint; `uv lock --check` checks dependency consistency; `uv build` produces wheel
and source artifacts. No new runtime dependency was added.

These results support reuse by other LangChain agents within the declared text /
function contract. They do not establish universal tool, model, LangChain-version
or provider compatibility, nor the correctness of arbitrary agent tasks.


To reproduce the passing Ollama checks from WSL:

```bash
OLLAMA_BASE_URL="http://YOUR_OLLAMA_HOST:11434" OLLAMA_MODEL=gpt-oss:20b uv run --locked --extra langchain --group integration python examples/langchain_tools.py --provider ollama
OLLAMA_BASE_URL="http://YOUR_OLLAMA_HOST:11434" OLLAMA_MODEL=gpt-oss:20b uv run --locked --extra langchain --group integration python examples/langchain_tools.py --provider ollama --async-stream
```

The leading `NAME=value` assignments override `.env` only for that command.
`YOUR_OLLAMA_HOST` is a placeholder: replace it with the reachable server host.
A WSL gateway can change after restarts; inspect `ip route show default`. The
validation host's actual address is intentionally omitted from the public text.
The example now reports actual tool inputs and observations on both success and
its explicit verification failures. Ruff lint and formatting passed after this
reporting-only example change; adapter/provider code was unchanged in the live follow-up.
