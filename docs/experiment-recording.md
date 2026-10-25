# Recording experiments with rosa-llm

The package records provider requests. The experiment runner owns trial design,
tool execution, safety, outcome verification and durable storage.

## Basic interface

```python
from rosa_llm import Config, create_client, package_info

records = []
config = Config(
    provider="openai",
    openai_model="gpt-6.1-sol",  # must be available to this account
    require_explicit_model=True,
)
with create_client(config, observer=records.append) as client:
    answer = client.generate("Your experiment prompt")

summary = answer.metadata["request"]
identity = package_info()
```

The same Client can be supplied to `ChatRosaLLM`. Ordinary LangChain tools require
no observer-specific changes. `ROSA_LLM_REQUIRE_EXPLICIT_MODEL=true` enables the
model guard through configuration loading; the default remains false for backward
compatibility. Without the guard, an unset OpenAI model still selects the first
visible catalog entry, and metadata explicitly labels this `catalog_first`.
Named configured models are labelled `explicit`, including named package defaults.

`Config.to_dict()` exposes effective provider/model, endpoint, profile, phase
timeout and selection policy, excluding credential paths and values. `package_info()`
exposes version, available dependency versions, Python/platform, a source SHA-256,
and Git revision/dirty state when this source checkout has a real Git repository.
The source hash is computed over sorted `rosa_llm/*.py` names and bytes separated
by NULs; it is stable between an ordinary source checkout and installed wheel.
Unavailable source/Git identities are null, never fabricated. Client snapshots
package identity at construction: freeze source and environment during a run.

## Observer contract (schema_version 1)

An optional synchronous `observer(record)` receives an independent dictionary
when an initiated stream is exhausted or closed. `generate()` closes its stream
on return/error, and LangChain's paths are regression tested. When calling
`Client.stream()` yourself, exhaust it or use `contextlib.closing`; abandoning an
unclosed iterator is not a reliable finalization mechanism. A hard process kill
cannot execute Python cleanup or flush an in-memory record. Persist trial starts
in the runner; classify killed trials as interrupted/unknown, and use external
incremental capture if native fragments must survive process death.

Records include:

- Unique package `attempt_id`, UTC start/end timestamps, effective configuration,
  package identity and `completed`, `failed` or `cancelled` status.
- Normalized input Messages/tools/options and completed normalized calls/usage.
- `native.request`: the actual inference JSON body, after provider conversion and
  model selection. It excludes authorization headers, OAuth requests and tokens.
- `native.raw_events`: decoded SSE JSON data payload strings or NDJSON lines,
  retained before parsing. These preserve malformed argument text, duplicate keys,
  incomplete native tool items and unsupported outputs. They are not raw HTTP
  bytes, headers, heartbeat events or an entire network trace.
- Inference HTTP status and bounded request identifier where supplied; failed
  records contain safe error classification. An API stream may have HTTP 200 yet
  fail with a provider error event. Arbitrary HTTP error bodies are not retained.

`LLMError.to_dict()` and CLI JSON preserve applicable `status_code`, `provider_code`,
`request_id`, `param`, `attempt_id` and `kind`, alongside existing code/message. Transport kinds
distinguish connect/read/write/pool timeout, connection error and other transport
errors. Original server messages/URLs/headers are not echoed. Authentication
renewal errors preserve safe status/code/request identity too.

Raw input/response records can contain private prompts, tool data and reasoning.
They are opt-in and are excluded from ordinary response summaries. Store them in
private files outside this repository; use owner-only permissions and an explicit
retention policy. Callback functions must be thread-safe when using concurrent or
async LangChain calls. The package does not serialize concurrent writes or retry
a failed sink. A callback exception aborts with `ConfigurationError(kind="observer_error")`;
the record passed to that callback retains the original request outcome/error.
Inputs should be JSON-compatible; sinks must account for invalid caller objects
if they deliberately test Python values outside that input contract.

## Timing definitions

| Field | Meaning |
| --- | --- |
| `elapsed_seconds` | Monotonic time from Client stream processing to terminal response processing, failure or cancellation; excludes the final observer callback and Client construction. |
| `setup_elapsed_seconds` | Time before initiating the inference HTTP request, including message conversion and any model catalog/OAuth/JWKS work. Absent if failure occurs before that boundary. |
| `inference_http_elapsed_seconds` | Time from inference HTTP initiation to terminal processing/failure; includes network, server queue/load/compute, parsing and stream consumer backpressure. This is not isolated model compute time. |
| `time_to_first_text_seconds` | Time to first provider visible-text delta. Null if none was observed; reasoning/tool argument bytes are not counted as visible text. |

Observer collection and stream consumer pauses add overhead. Use identical
instrumentation for baseline and modified ROSA. Ollama's native durations remain
in response metadata separately. Configured HTTP timeouts apply per transport
phase, not to the whole trial. Worker-thread async cancellation cannot instantly
interrupt a blocking HTTP read; the runner owns a total deadline and must account
for outstanding work before subsequent trials.

## What the runner must record and control

`completed` means a validated provider request completed, **not** that a tool ran
or a research task succeeded. LangChain may reject calls through an allowlist,
validate/coerce arguments, execute tools, handle errors, or return directly after
a tool. Capture LangChain agent/tool callbacks, actual observations and errors,
and the independently evaluated task outcome. A tool result may appear in the
next model input, but that is not a complete execution trace.

Pin models and consumer dependencies; record robot/ROS state, prompts, converted
schemas, tool source, seeds/options and repeats. Keep caching disabled in measured
LangChain runs. Validate source revision/hash before each experiment batch.
Capture every failed/interrupted attempt, including runner/configuration failures
before a provider request starts. Do not score sanitized infrastructure errors as
model failures or treat normalized arguments as byte-exact provider output.

Request summaries also expose `transport.timeouts` from the HTTPX client's actual
connect/read/write/pool defaults, plus whether the caller supplied the client.
This can differ from Config's declared timeout. Redirects are disabled per request,
including OAuth discovery, keys, renewal and revocation; an injected client's
`follow_redirects=True` cannot forward token forms to another origin. Custom client
hooks/transports remain the caller's responsibility: `native.request` is the body
supplied to HTTPX, not proof that a custom hook/transport left it unchanged.

Call `client.models()` outside timed trials to record Ollama digest, size,
capabilities and modified time where supplied. Record the Ollama server version
and settings separately; the package adds no hidden catalog request to each
Ollama generation. Inference metadata labels provider-reported vs request-fallback
model identity. A mutable tag/catalog and an OpenAI backend cannot be made immutable
by this adapter; verify model provenance before/after a batch as appropriate.

Native saved history is a continuation aid, not a portable conversation format.
Editing Message content/calls/roles/results now invalidates its native snapshot;
current public fields are sent instead. Unchanged reasoning/native items are
preserved. Do not mutate evidence snapshots themselves. Modified reasoning-model
histories may legitimately be rejected by the provider; never silently restore
old content or normalize invalid model arguments to obtain a passing run.
