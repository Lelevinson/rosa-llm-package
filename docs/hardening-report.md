# Research hardening results — 0.2.0

For the later standalone Git/publication preparation, see
[pre-push review](publication-preparation.md). The results below retain their
original validation scope.

Date: 2026-10-24 (Asia/Taipei). This supersedes the unresolved package-defect
findings in the earlier compatibility/readability/readiness reports. The package
remains infrastructure; no JTVR logic or ROSA Research source was changed.

Python support was subsequently extended to >=3.11 without runtime changes.
See [Python compatibility](python-compatibility.md) for the new version matrix;
the dated hardening results below retain their original test environment.

## Fixed defects

| Correction | Source and regression evidence |
| --- | --- |
| Public standalone history edits now override stale native replay; unchanged native reasoning remains intact. Normalized Ollama argument edits no longer mutate native evidence. | `types.py`: Message snapshots/native_history; `providers.py`; `tests/test_hardening.py`: standalone history, call-edit and raw-evidence tests. |
| HTTP errors retain bounded status/code/request ID/parameter fields; transport failures distinguish connect/read/write/pool timeout and connection errors. CLI JSON and terminal OAuth renewal errors preserve safe details. Full server messages, auth headers and credential values are excluded. | `errors.py`, `http.py`, `auth.py`, `cli.py`; error/CLI/transport regressions. |
| Ollama catalog output retains available digests, capabilities, size and modified time. Model identity fallback is labelled. Effective configuration records catalog-first vs named selection, with an opt-in explicit-model guard. | `providers.py`, `config.py`; catalog, model-selection and CLI override tests. |
| Ambiguous/nonfinite JSON fails instead of silently overwriting duplicate keys or accepting NaN/Infinity/overflow. Malformed caller JSON values are classified as configuration failures before inference HTTP. | `http.py`: strict_json_loads/validate_json_body; duplicate/nonfinite/request-value regressions. |
| Standalone output rejects invalid/repeated call identities, malformed token measurements, unfinished tool calls and unsupported output items instead of releasing misleading output. A contradictory terminal snapshot cannot hide a completed native tool call. | `client.py`, `providers.py`; invalid identity/count, unfinished/unhandled output and snapshot regressions. |
| Standalone streaming checks text consistency; CLI streaming prints terminal-only text without duplicating deltas. | `client.py`, `cli.py`; conflicting terminal text and terminal-only CLI regressions. |
| Optional per-request observation retains failures, native incomplete/malformed output, cancellation and exact effective inference bodies. Completed-response summaries provide configuration, source/dependency identity and explicitly defined monotonic timing. Failure attempt IDs correlate records and exceptions. | `client.py`, `provenance.py`; record privacy, failed arguments, incomplete calls, cancellation, async batch and observer-failure tests. |
| Injected HTTP clients no longer override the no-redirect rule on OAuth/provider calls. Actual client timeout defaults are recorded separately from Config, preventing misleading transport provenance. | `auth.py`, `providers.py`, `client.py`; token-redirect and injected-timeout tests failed before the correction, then passed. |

The initial defect regressions were run against the old implementation: 10 failed.
Related aliasing/identity/output and streaming/snapshot probes also failed before
their corrections. Successful model output is not repaired, retried or replaced
to obtain a passing result. The full test count increased from 212 to **254**.

## Verification actually performed

| Check | Result |
| --- | --- |
| Baseline, ROS Jazzy sourced | 212 passed; existing upstream RunCollector/Pydantic warning. |
| Final applicable suite, uv locked extras/integration group, Jazzy sourced | **254 passed**, including unchanged ROSA executor tests; same upstream warning. |
| Ruff lint / format; uv lock validation | Passed. Dependency versions in the lock stayed unchanged; root version moved to 0.2.0. |
| Wheel and source distribution | Built successfully; artifact paths are `dist/rosa_llm-0.2.0-py3-none-any.whl` and `dist/rosa_llm-0.2.0.tar.gz`. |
| Fresh uv base-wheel environment, Python isolated mode | No LangChain installed/imported; client/provenance check passed. Suite: **147 passed, 5 optional skips**. |
| Fresh uv optional-wheel environment with LangChain 0.3.30 | Installed wheel, not editable source. Suite: **251 passed, 2 skip reports** (ROSA absent and optional ChatOpenAI comparison absent). |
| Live OpenAI generic nested-tool agent | Sync and async/event-stream modes passed using `gpt-6.1-sol`, with actual tool inputs/observations checked for Unicode, numeric values, false and JSON null. Automatic renewal was exercised without interactive login; local session-state details are omitted. |
| Live request observer | Async agent captured two completed requests, explicit model bodies, native event counts 37/28 and timing fields. Raw records were kept in memory and not printed. |
| Live unmodified ROSA | Exposed only `ros2_node_list`; exactly one actual tool run returned `nodes=[]`, matching direct CLI before/after. No physical actions were offered. |
| Live Ollama | **Unavailable** on this date: localhost ConnectError; route-derived WSL gateway ConnectTimeout. Native behavior remains covered by automated HTTP tests. Earlier live results remain historical, not today's validation. |
| Docker | **Unavailable** in this WSL distro. Existing nonroot/persistent-auth setup retained; optional `INSTALL_LANGCHAIN=true` build added. Image build/runtime and persistence are not claimed tested. |
| GitHub CI | Local workflow added with read-only repository permissions; YAML inspected/parsed. No remote workflow run, repository creation or push occurred. |
| Credential/artifact scan | No matching long API-key, JWT or private-key patterns in scanned source/project files or final wheel/sdist; no credential, host, env or Git files in artifacts. Bounded pattern checks do not prove every possible secret format absent. |

The full environment used Python 3.12.3, uv 0.13.2, langchain-core 0.3.86,
LangChain 0.3.30 and Pydantic 2.14.1. Fresh wheel installs used independent
dependency resolution and still passed their applicable tests. Native Windows
Python and other LangChain generations are not validated.

Sandbox restrictions caused DNS failures during the first lock refresh and a
sandboxed async test invocation stalled during worker shutdown. The stalled test
was interrupted; required operations passed in the permitted execution context.
These are environment failures, not passing test evidence or provider failures.

## Readiness and remaining responsibilities

**ROSA integration:** ready for development through the tested text/function
contract. No known research-critical package defect remains from the verified
findings. The corrected behaviors and additive APIs are documented in
[experiment-recording.md](experiment-recording.md). Existing callers retain
their interfaces; contradictory/malformed data now fails explicitly.

**Formal experiments:** the external runner must freeze a revision or released
artifact plus consumer lockfile; select explicit models; preserve request records,
LangChain tool callbacks/results/errors and independently checked outcomes; record
Ollama model/server identity and ROS state; define total trial deadlines, warmup,
retry/cancellation policy and measurement overhead identically in both conditions.
`completed` is provider completion, never proof of tool execution or task success.
Do not use the mutable default OpenAI catalog selection for controlled runs.

**Standalone GitHub repository:** technically prepared for teammate installation,
with isolated installs and local checks passing. Publication is still conditional:
this workspace's `.git` is an empty read-only placeholder, so it has no revision
or history to audit. The owner selected Apache-2.0; the full license is included
in the source tree and declared in package metadata. Create/review the actual
Git baseline and staged contents, then run CI and Docker
checks in their real environments. No repository was created or pushed here.

**Unsolved provider/framework boundaries:** ChatGPT-plan restrictions and backend
availability; mutable model aliases/catalogs; no immediate interruption of a
blocking worker-thread HTTP read; unavailable usage/immutable backend identity;
no strict/forced tool selector through native Ollama; no named forced choice on
the current OAuth namespace path; text/function scope and pinned LangChain core.
Current OAuth restrictions were checked against the
[official preview documentation](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
These limitations are not silently emulated by changing arguments or prompts.

## Reproduction

```bash
source /opt/ros/jazzy/setup.bash
uv sync --locked --extra langchain --group integration
uv run --locked --extra langchain --group integration pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv lock --check
uv build
```

On a Docker host, build the base CLI image normally, or enable the optional adapter:

```bash
docker build --build-arg INSTALL_LANGCHAIN=true -t rosa-llm:0.2.0 .
```

`--build-arg` sets the named Dockerfile build option. The image still contains no
ROSA or ROS runtime; an existing ROSA environment installs this package's extra
using its own uv project. Use a reviewed Git commit/tag or the built wheel as its
dependency and retain that consumer project's lockfile.

Final package source SHA-256 (also matched in both isolated installations):
`95820a99b4ba94f8b3abebbfb26282cf9265f67ffc475fe9b042cc3074842fed`.
No Git revision was fabricated for this non-repository workspace.
