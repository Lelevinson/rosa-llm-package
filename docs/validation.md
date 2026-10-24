# Implementation and validation report

Current 0.2.0 verification is in [hardening-report.md](hardening-report.md).
The dated results below describe earlier snapshots.

## 2026-10-23 general adapter audit

The current [LangChain compatibility report](langchain-compatibility.md) explains
the audited contracts, architectural fixes, provider capabilities and limits.
The implementation remains in this standalone package; ROSA source and runtime
dependencies were not changed.

| Final check | Result |
| --- | --- |
| Full suite, Jazzy sourced, optional adapter/integration dependencies enabled | **212 passed**, including two unmodified ROSA executor tests. One upstream LangChain RunCollector Pydantic `.copy()` deprecation warning remains. |
| General tool evidence | Real LangChain tools, parsers, sync/async agents, callbacks, event streams, batches, result messages and stream-close behavior, with mocked provider HTTP. See the compatibility report for the case matrix. |
| Ruff lint and formatting | Passed; 30 Python files formatted. |
| `uv lock --check` | Passed; no dependency changes. |
| `uv build` | Final wheel and source distribution rebuilt in `dist/`. |
| Final base wheel in disposable uv environment | Imported in Python isolated mode and constructed a client; LangChain was neither installed nor imported. |
| Final optional wheel in another disposable uv environment | Bound an ordinary LangChain tool, received a mocked native Ollama call through `invoke`, executed the actual tool to a ToolMessage, and verified unsupported schema fields raise an error. |
| Live OpenAI generic sync agent | Passed with `gpt-6.1-sol`; actual nested-tool observation matched the requested Unicode, numeric, boolean and nullable values. |
| Live OpenAI generic async agent/events | Passed with the same independently checked tool observation. |
| Live OpenAI strict tool/result loop | Passed with required choice, parallel=False, real LangChain tool execution and final choice=none. |
| Live named forced choice | Negative evidence: HTTP 400 with namespace-wrapped tools, for bare and qualified names. Now rejected locally; no silent fallback. |
| Live Ollama follow-up | The test host's WSL gateway (address omitted) was reachable; installed `gpt-oss:20b` and `qwen3.5:9b`. Localhost remains unreachable and configured `qwen3:8b` is not installed. Temporary environment overrides used; `.env` unchanged. |
| Live Ollama GPT-OSS sync/async agents | Both passed exact nested-tool observation checks, including Unicode, negative/zero numbers, false and JSON null. |
| Live Ollama Qwen sync/async agents | Failed exact-value criterion. Async evidence: one tool execution with string `"None"` instead of JSON null; remaining values matched. Initial sync failure lacked detailed input capture. No value repair or success claim. |

The automated suite used locked project dependencies through uv. Wheel checks
used separate fresh dependency resolutions, with the pinned LangChain core extra.
Live checks used the existing authorized OAuth profile; credential values were
not printed. Temporary validation-only diagnostic scripts were removed.

## Earlier implementation validation (2026-10-22)

Project: `rosa-llm-package` checkout (machine-specific path omitted)

Package/distribution: `rosa_llm` / `rosa-llm` 0.1.0

Validation date: 2026-10-22 (Asia/Taipei)

## Delivered

- A uv-managed Python 3.12 package with `pyproject.toml`, `uv.lock`,
  `.python-version`, `src/` layout, `.env.example`, local `.env`, and ignore files.
- Shared Python API, explicit chat history, streaming, normalized text/tool calls,
  model identity, usage and metadata. Function tools work with both adapters;
  custom text tools are implemented for OpenAI. Unsupported options raise errors.
- Official ChatGPT OAuth: initial dynamic registration, persistent host identity,
  PKCE, one-time state/nonce, signed ID-token validation, granted-scope checks,
  profile isolation, renewal, rotating tokens, process locking and revocation.
- Protected local storage outside the repository, atomic replacement and no
  credential values in CLI output. Failed login preserves a valid old session.
- All requested CLI commands, JSON automation mode, an explicit short live smoke
  command, Python examples, README, protocol references, Dockerfile and Compose files.
- The standalone API retains no ROSA or LangChain runtime dependency. The later,
  explicitly requested optional LangChain adapter is included, with ROSA in a
  separate development dependency group. Existing projects were not modified.

Architecture and dependency purposes are described in [README](../README.md).
Runtime dependencies: `httpx`, `python-dotenv`, `PyJWT[crypto]`; development tools:
`pytest`, `ruff`; build backend: `uv_build`. Transitive versions are locked.

## Results

| Check | Result |
| --- | --- |
| Standalone checks | All 107 passed, including signed test JWTs, a mocked identity service with a real local callback listener, three concurrent refresh processes, and regressions for both observed live stream formats. |
| Full suite with optional integration enabled | 130 passed: the 107 standalone checks plus 21 adapter checks and two actual ROSA executor tests with mocked HTTP. One upstream Pydantic deprecation warning comes from LangChain's local run collector. |
| `uv run ruff check .` | Passed. |
| `uv run ruff format --check .` | Passed. |
| `uv lock --check` | Passed. |
| `uv build` | Wheel and source distribution built successfully under `dist/`. |
| Isolated wheel installation | Imported `rosa_llm`, reported version 0.1.0, and created an Ollama client in a separate uv environment with Python isolated mode. |
| Base wheel dependency isolation | Rebuilt base wheel imported and created a client in an isolated environment with no LangChain installed or imported. |
| Optional wheel extra | An isolated installation of the wheel with `[langchain]` resolved langchain-core 0.3.86 and successfully constructed `ChatRosaLLM`. |
| CLI initialization/status | `.env` created; Ollama selected with model `qwen3:8b`; machine-readable status verified. |
| Unattended OpenAI smoke command before login | Exited 1 with `authentication_required`; did not launch OAuth. |
| Real OpenAI login/status | Live authorization/status checks passed and credential-file permissions were verified as owner-only (0600). Account, profile and session-state details are omitted from this public report; credential values were neither printed nor committed. |
| Real OpenAI model catalog | Catalog retrieval succeeded; subsequent checks used the explicit model names recorded below. Account-specific catalog membership and ordering are omitted. |
| `uv run rosa-llm test --provider openai --json` | Passed using `gpt-6.1-sol`: streamed text was `ROSA_LLM_OK`, followed by `response.completed`. |
| `uv run rosa-llm test --provider openai --tool --json` | Passed: one validated echo call, explicit tool result/history and final `ROSA_LLM_OK`. |
| ROSA adapter | Implemented in `rosa_llm.langchain.ChatRosaLLM`; ROSA 1.0.10 source unchanged. See the [source audit and setup guide](langchain-integration.md). |
| Actual ROSA with OpenAI | Both `ROSA.invoke` and `ROSA.astream` executed `ros2_node_list` exactly once and found a temporary nonpublishing node. Captured tool results and final answers agreed with direct CLI output. The probe was removed afterward. |
| Live Ollama smoke command | Failed to connect at `http://localhost:11434`; no completed inference. |
| Additional Ollama catalog probes | Localhost returned `ConnectError`; the test host's WSL gateway (address omitted) returned `ConnectTimeout` with a five-second timeout. |
| Docker build/configuration/persistence | Unverified: Docker reports that it is unavailable in this WSL distro and asks for Docker Desktop WSL integration. |

Tests and build ran on WSL2 Linux with Python 3.12.3 and uv 0.13.2.
Local test services and dependency downloads required the execution environment's
permitted network context. The standard uv cache was read-only in the sandbox,
so checks used a writable temporary uv cache; normal user terminals can use
their ordinary cache. An isolated offline installation lacked cached registry
artifacts; the permitted network installation succeeded.

Real OpenAI inference exposed two adapter bugs that the initial fixtures did not
represent: the observed HTTP 200 stream omitted `Content-Type`, and completed
output items arrived in `response.output_item.done` while the final response's
`output` array was empty. Regression tests reproduced both failures before the
fixes. The adapter now tolerates missing MIME metadata and retains completed
output items. An incompatible explicit MIME type still fails, and a valid
`response.completed` event remains mandatory. Both original live smoke commands
passed after the fixes. No OAuth or inference request arguments were rewritten
to obtain the passing results.

## Limits and remaining manual steps

1. Start or expose your Ollama server at an endpoint reachable from WSL, then set
   `OLLAMA_BASE_URL` and an installed `OLLAMA_MODEL` in `.env`. Run
   `uv run rosa-llm models --json`, then `uv run rosa-llm test --json`.
2. OpenAI login, catalog access, streamed inference and a function-tool round trip
   are now verified for this saved profile. Other team members must log in with
   their own accounts. Expiry-driven refresh/rotation, revocation, usage limits
   and custom text tools remain covered by mocks rather than live tests.
3. Real login completed on this installation. The exact browser and forwarding
   path used by the user was not independently observed; verify that path when
   onboarding another WSL machine.
4. On an available Docker host, follow README to build, log in with host
   networking, recreate the CLI container, and verify the same profile/host
   persists. Host networking has platform-specific requirements; it is not
   enabled by this project. No host or Docker configuration was changed.

Credential files contain plaintext secrets protected by Unix ownership and
permissions. Use private, local persistent storage and one OS user/volume per
person. Linux locking is not a distributed lock. A process killed after the
server rotates a token but before durable local replacement can require renewed
login. If JWKS validation temporarily fails after a refresh, the replacement
tokens are retained as pending; inference waits for successful validation.

The native client is synchronous and supports text, explicit history and local
function/custom text tools. The optional LangChain adapter supports sync and
worker-thread-backed async calls with text/function tools. Images/files, hosted
tools, arbitrary OpenAI API options and native Windows Python are outside scope.
JWT validation currently allows RS256. The OpenAI route is a documented preview;
the passing live checks cover this account/model and the tested text/function
features, not every account, model, deployment or failure condition.
