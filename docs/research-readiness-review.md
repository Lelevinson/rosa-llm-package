# Research infrastructure readiness review

Historical audit: the confirmed fixable findings below were addressed in 0.2.0.
See [hardening results](hardening-report.md) for current evidence and remaining limits.

2026-10-23. Source-level audit of the current package, not an endorsement based
on earlier reports. No implementation, dependency or ROSA Research changes.

## Decision

1. **ROSA research development: conditionally ready.** The tested text/function
   LangChain path is suitable for prototyping with an explicit model and endpoint.
   No unconditional blocker was found for that path. It is not a robot safety
   boundary or evidence that an agent's requested task actually succeeded.
2. **Controlled experiments: not sufficient by itself.** It can serve as the
   transport/model adapter under an external experiment recorder, provided the
   provenance, failure-classification and timing gaps below are addressed before
   collecting results used for scientific conclusions.
3. **Separate public repository: a reasonable candidate, not release-ready yet.**
   Credential safeguards and the inspected distribution are encouraging, but
   there is no actual Git revision/history to audit, no license file/declaration,
   and no CI workflow. Establish the repository, choose its license, inspect the
   staged release and establish repeatable checks before publishing.

## Confirmed findings

| Finding and evidence | Consequence / action before affected experiments |
| --- | --- |
| **Standalone saved history overrides visible edits.** `OpenAIProvider.input_items` prioritizes `provider_items` (`providers.py:203`); Ollama does likewise (`providers.py:62`). A targeted check normalized an assistant response, changed `message.content` to `edited`, and observed `original` on the wire. | Actual behavior inconsistency with the LangChain path, whose snapshot check honors edits (`langchain.py:254`). Resolve or explicitly prohibit standalone Message edits before history-ablation/context-editing experiments. Ordinary unchanged continuation is unaffected. No fix made during this review. |
| **Failure detail is lost.** `http.py:51` collapses otherwise-unclassified statuses to a generic ProviderError. A targeted check showed HTTP 400 and 500 with the same unrecognized provider code produce identical public class/code/message, without a status attribute. `client.py:73` also conflates connection failure and timeout. | Do not classify every such failure as a model/tool error. Preserve sanitized status, provider code and request identifier through a controlled transport recorder or a focused later error-metadata change. Never log authorization headers to obtain this evidence. |
| **Model provenance is incomplete.** `OllamaProvider.models` drops native top-level digest (`providers.py:36`), confirmed using mocked `/api/tags`. OpenAI's unset model selects catalog entry zero on each request (`providers.py:188`). | Pin an explicit model. Record Ollama digest/server version separately; a tag alone does not identify weights. Save the provider-returned model too. OpenAI backend identity cannot be made immutable solely by this package. |
| **Run records are incomplete by design.** Response/CLI JSON includes model, calls, usage and selected metadata, but no effective configuration, source revision, wall-clock timestamps or total elapsed time (`types.py:68`, `cli.py:240`). Failed requests do not return a Response. | An external recorder must retain each attempt, including failures, effective configuration, prompts, converted schemas, provider-native arguments where available, and timings. Final answer text is not an experiment record. |

These are concrete findings, not claims of a credential compromise. None required
an emergency source change for the existing unchanged-history LangChain workflow.
The affected experimental workflows must address them before collecting data.

## Correctness and measurement boundaries

- Source inspection confirms no generation fallback between providers, no model
  argument-repair rules, and no automatic generation retries in the package's
  default path. OAuth renewal and logout retry behavior are separate operations
  (`auth.py:336`, `auth.py:379`). A caller-injected HTTP client may behave differently.
- OpenAI JSON arguments are decoded once; Ollama JSON-string envelopes are decoded
  once. A targeted duplicate-key check yielded the last value, while original
  OpenAI argument text remained in `Message.provider_items`. Thus normalized
  dictionaries are not byte-exact model output. Malformed-JSON failures lose their
  raw arguments from the normal public error/CLI result. Record safely at the
  transport boundary when those failures are experimental evidence.
- Deliberate conversions include system-to-developer on OpenAI, ordered text-block
  flattening, refusal text merging, local tool namespaces, UUIDs for missing
  Ollama call IDs, and LangChain schema conversion. Explicit strict schemas use
  LangChain's schema transformation. Save converted schemas, not just Python
  annotations. Tool validation/defaults/coercion subsequently belong to LangChain
  and the tool; record input and executed observation separately.
- Completion requires the terminal provider marker. Empty output without calls
  and malformed arguments fail; tools are released only after completion
  (`client.py:63`, `providers.py:128`, `providers.py:378`, `langchain.py:428`).
  This prevents partial tool requests from being treated as completed actions.
- **Timing is not interchangeable across providers.** Ollama exposes selected
  native durations; OpenAI currently exposes no equivalent client duration.
  OpenAI calls include catalog lookup and potentially OAuth/JWKS traffic before
  inference. Time-to-first LangChain tool call is terminal-call delivery time,
  not first tool-argument-byte latency. Visible-text events omit reasoning tokens.
  Record end-to-end monotonic timing and distinguish these definitions.
- `Config.timeout` becomes an HTTPX phase timeout (`client.py:20`), **not a total
  trial deadline**. A stream that keeps delivering data can exceed that duration.
  LangChain async uses worker threads; cancellation need not immediately stop a
  blocking request. Use an external trial deadline and account for outstanding
  work before starting subsequent latency measurements.
- Available usage is preserved and partially normalized (`langchain.py:371`).
  Missing usage is unknown, not zero. Cached/reasoning details are only available
  where reported; token counts across different tokenizers are not equal units
  of work. Keep default caching disabled for measured trials.

Provider limitations remain distinct: native Ollama lacks the implemented strict /
forced-choice controls; the current OAuth namespace path rejects named choice;
the adapter is text/function focused and pinned to LangChain core 0.3.86.
Unsupported options fail explicitly. This audit did not repeat live model calls
or revalidate current service eligibility. Model mistakes such as returning the
string `None` when asked for JSON null must remain task failures, not adapter fixes.

## Authentication, credentials and distribution

`auth.py` validates state, PKCE, nonce, issuer, audience, signature, expiry and
returning identity. Refresh happens under a re-read process lock, persists rotated
tokens before validating replacement identity, and blocks inference while that
validation is pending. `storage.py` uses owner checks, no-follow file opens,
owner-only permissions, atomic replacement and fsync. The tests cover these paths,
including concurrent refresh, key-server outage and a real loopback callback
with mocked identity HTTP. Default HTTP disables redirects and environment proxies.

On this machine, credential directory mode was **0700**, and credentials/host
files were **0600**. No credential values were printed. Plaintext local storage
and Linux `fcntl` locking imply a private, trusted OS account/filesystem; this is
not distributed locking or native Windows Python support. Ollama's HTTP endpoint
does not provide TLS or application authentication here: use a trusted endpoint
or appropriately protected transport for sensitive prompts.

A bounded scan of 41 project files and wheel contents found no matching long
API-key, JWT or private-key patterns. Wheel filenames contained no `.env`,
credential or host files. `.gitignore` excludes local env/auth/build files and
Docker context uses an allowlist. This is not proof that every possible secret
format is absent, and Git history/staged contents could not be checked because
`.git` is an empty placeholder, not a repository. Recheck the actual release.

The existing readable modules and separate optional adapter are maintainable at
this scale. Preserve focused tests for upstream changes. CI is a recommendation,
not a discovered runtime defect; license selection belongs to the owner.

## Reproducibility and evidence

The locked checkout targets Python 3.12 and contains `uv.lock`; wheel dependency
ranges can resolve differently in consumer environments. Record the consumer's
lockfile as well. Python patch version, OS/ROS installation, model/server state,
tool implementation and environment also matter. Docker base tags are not pinned
by digest. A saved package version `0.1.0` alone cannot distinguish these edits.

Current checks:

- **212 tests passed**, with ROS Jazzy sourced, using
  `uv run --locked --extra langchain --group integration pytest -q`.
  One existing upstream RunCollector/Pydantic deprecation warning remains.
- Ruff lint and `uv lock --check` passed. Dependencies observed: Python 3.12.3,
  langchain-core 0.3.86, LangChain 0.3.30, HTTPX 0.28.1, Pydantic 2.14.1.
- Additional offline checks directly exercised native normalization/history
  conversion, generic HTTP errors and an Ollama catalog through HTTPX MockTransport.
  They are targeted audit probes, not live provider measurements.
- Source fingerprint, SHA-256 of sorted `src/rosa_llm/*.py` relative paths and
  contents, separated by NUL bytes:
  `48a0d58e7180894fe6dd53f69f478d5d3058ccfcc7884fc94e585eb0e8531ff3`.

**Recommended next action:** establish a reviewed Git baseline and consumer lock;
define the external run manifest/error/timing recorder; resolve the standalone
history issue if context editing is needed. Then run a small matched-model pilot
that retains all successful, failed and cancelled attempts before formal trials.
No redesign of this package or changes to ROSA Research are needed for this step.
