# rosa-llm

Licensed under [Apache-2.0](LICENSE).

Version 0.2.0 hardens history replay, diagnostics and request provenance. See the
[hardening results](docs/hardening-report.md) and the
[experiment recording contract](docs/experiment-recording.md) for the current
interfaces, corrected behaviors and remaining provider/environment limits.

A standalone Python 3.11+ package for Ollama and official Sign in with ChatGPT
OAuth. No ROS, ROSA, LangChain, database, API key, or agent runtime is required.
ChatGPT-plan inference requires an eligible account and an explicit permission
grant; a successful identity login alone is insufficient.

An optional [LangChain adapter and ROSA integration guide](docs/langchain-integration.md)
is available. The [general compatibility report](docs/langchain-compatibility.md)
covers supported custom tools, provider differences, test evidence and limits.
ROSA 1.0.10 has also been tested without modifying its source.

The base client and optional LangChain adapter support Python 3.11 and 3.12.
The checkout keeps `.python-version` at 3.12 for ROS 2 Jazzy; the Docker image
also stays on 3.12. Standalone installations can use Python 3.11. See the
[Python compatibility and verification report](docs/python-compatibility.md)
for the supported scope and the separate ROSA Research dependency-resolution boundary.

## Install in WSL Ubuntu

With Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/)
available, open a WSL terminal. The intended GitHub repository is public;
HTTPS cloning does not require GitHub sign-in once publication is approved:

```bash
git clone https://github.com/Lelevinson/rosa-llm-package.git
cd rosa-llm-package
uv sync --locked
uv run rosa-llm init
uv run rosa-llm status --json
```

`uv sync` installs this project and its locked dependencies into `.venv`;
`--locked` refuses to change `uv.lock`. `uv run` runs the following command in
that environment. `init` creates `.env` and refuses to overwrite existing settings.
`git clone` downloads the repository into a new local directory; `cd` enters
that directory. For an existing checkout, start with `cd` and skip cloning.
No global Python installation is modified. Public repository reads and model
access are separate: each user supplies their own ChatGPT OAuth session or a
reachable Ollama server. GitHub authentication is needed for repository writes.
Never put a GitHub token in a Git URL, `.env`, `pyproject.toml` or `uv.lock`.

## Configure and switch providers

Edit `.env` in the directory from which you run the program:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:8b
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_MODEL=
ROSA_LLM_AUTH_DIR=
ROSA_LLM_PROFILE=default
ROSA_LLM_TIMEOUT=120
```

For Ollama, the endpoint must already be reachable and the selected model must
already be installed. Use your actual model name from `rosa-llm models`.
For OpenAI, set `LLM_PROVIDER=openai`, then log in as described below.
Empty `OPENAI_MODEL` selects the first visible model in the account's ordered
catalog on each request. An explicit name must occur in that catalog.

Precedence, highest first: process environment, local `.env`, per-user
`$XDG_CONFIG_HOME/rosa-llm/config.env` (normally `~/.config/rosa-llm/config.env`),
then defaults. `uv run rosa-llm init --user` creates the per-user file.
The loader does not modify process environment or expand `${...}` in dotenv values.
An empty `ROSA_LLM_AUTH_DIR` uses the default user directory. Tokens never go in `.env`.

## Sign in with ChatGPT

```bash
uv run rosa-llm login
uv run rosa-llm status --provider openai --json
uv run rosa-llm models --provider openai --json
uv run rosa-llm ask "Explain ROS 2 in one sentence." --provider openai --json
```

`--provider` overrides the configured provider for that command. Login always
authenticates OpenAI, even while `.env` selects Ollama. Every team member signs
in using their own account; the program never requests a ChatGPT password.
It opens the system browser and listens only at `127.0.0.1:1455/auth/callback`.
`--port 0` selects a free loopback port. Each attempt uses the exact callback
URI in both authorization and code exchange.

In WSL, the browser can run on Windows. If the browser does not open:

```bash
uv run rosa-llm login --no-browser
```

Open the printed authorization URL in your Windows browser while the command
remains running. `--no-browser` disables browser launching. Keep the URL private;
printed URLs omit retained identity-token hints. WSL localhost forwarding must
reach the listener in Ubuntu. This is an environment requirement, not guaranteed
by the Python package. Do not change the callback host to `localhost`, paste a
callback URL into chat, or expose the listener publicly.

If forwarding fails, use an approved local SSH tunnel: on the browser's host,
run `ssh -N -L 1455:127.0.0.1:1455 user@your-wsl-or-linux-host`, then run login
on that Linux host with `--no-browser --port 1455`. `ssh -N` runs only forwarding;
`-L` maps the browser host's local port to the remote loopback listener. This
requires your existing SSH access; the package does not install or configure SSH.

Use `--profile lab` on login and subsequent commands to keep another registration
separate, or set `ROSA_LLM_PROFILE=lab`. Labels use letters, digits, `_`, and `-`.
To add a different account/workspace, choose a new label. Returning logins reuse
that profile's client ID and verify its subject. `status` shows the active label
without tokens and does not contact OpenAI or assert current remote eligibility.

If identity login succeeds without plan permission, a new identity registration
is retained with inference disabled. A failed login never replaces an existing
valid session. To explicitly ask for permission again:

```bash
uv run rosa-llm login --consent
uv run rosa-llm logout --json
```

`--consent` requests the consent screen again; ordinary returning logins do not.
`logout` attempts remote revocation, clears the selected profile's tokens, and
retains its registration and host identity. If remote revocation cannot be
confirmed, it reports that fact; disconnect the app in ChatGPT Settings.

## CLI and live smoke checks

```bash
uv run rosa-llm models --json
uv run rosa-llm ask "Say hello" --json
uv run rosa-llm ask "Explain a ROS 2 node briefly" --stream
uv run rosa-llm test --json
uv run rosa-llm test --tool --json
```

`models` lists available models. `ask` performs real inference. `--json` emits
one JSON document on stdout, with provider, model, response, requested tools,
usage and available metadata. Diagnostics go to stderr; failures exit with code 1
and emit an `error` object. No inference command opens OAuth or prompts for input.
`--stream` prints text as it arrives and cannot be combined with `--json`.
Partial streamed text is not proof of completion: check the process exit code.

`test` makes one real request and checks an exact short marker. `--tool` instead
allows one harmless echo call and one follow-up request, validates the requested
name and arguments, and checks the final marker. Models that decline the call
fail the smoke check. Neither command downloads models or starts an agent.
`--model NAME` overrides the selected provider's model for one command.
`--env-file PATH` selects another local dotenv file. Options follow the command.
Use `uv run rosa-llm ask --help` for command syntax.

## Python API and another project

```python
from rosa_llm import Message, Tool, create_client

with create_client() as client:
    response = client.generate("Explain what ROS 2 is.")
    print(response.text)
    print(response.tool_calls, response.model, response.usage)

    history = [Message("user", "What is a node?")]
    first = client.generate(history)
    history += [first.message, Message("user", "Give one example.")]
    print(client.generate(history).text)
```

History is explicit; the client does not mutate it or retain a hidden session.
Keep `response.message` so provider-specific reasoning and tool-call data survive
the next request. Plain user/assistant text can be shared across providers;
opaque provider output cannot. System instructions map to developer messages on
the OpenAI OAuth route. Ollama supports system messages and rejects developer messages.

`client.stream(prompt)` yields `StreamEvent` objects: `text` contains deltas;
`completed` contains the final normalized `response`. Consume through completion
and handle `LLMError` exceptions. A completed empty response without tools is rejected.
Only the final response has complete tool calls; partial arguments are not executed.

`Tool(name, description, parameters)` declares a JSON function. OpenAI wraps it
in a namespace (default `local`); `Tool(..., kind="custom", format={"type":"text"})`
declares a custom text tool. Ollama rejects custom tools and explicit namespaces.
The caller must validate and execute requested tools, then append a `Message`
with role `tool`, `tool_call_id`, and `tool_name`; custom outputs also set
`tool_kind="custom"`. See [examples/tool_call.py](examples/tool_call.py) and
[examples/chat.py](examples/chat.py). Run examples with `uv run python examples/chat.py`.

From another uv project whose declared Python range is compatible with >=3.11:

```bash
uv add "rosa-llm @ git+https://github.com/Lelevinson/rosa-llm-package.git@v0.2.0"
# Include the optional LangChain adapter when needed:
uv add "rosa-llm[langchain] @ git+https://github.com/Lelevinson/rosa-llm-package.git@v0.2.0"
```

`uv add` records the package as a dependency of the current project. uv checks
that project's declared Python range as well as the selected interpreter. A
consumer declaring >=3.9 still needs a compatible range or dependency marker;
this package change does not perform that consumer-side integration. Its local
`.env` is read from the calling directory. Alternatively pass `Config(...)` to
`create_client(config)` or pass `env_file="/path/to/.env"`.
These Git installation commands require the approved `v0.2.0` tag to exist on
GitHub. `@v0.2.0` selects that version reference; uv records its resolved commit
in the consumer lockfile. A consumer can instead pin the full commit SHA for an
immutable source reference. `uv add --editable /path/to/rosa-llm-package` remains
available for local package development, but shared research dependencies should
use the Git reference. Public HTTPS installation does not require a GitHub
token. Model credentials remain local to each user and are never shared.

## Docker

The image uses Python 3.12, uv, and a non-root user (UID 10001). A private named
volume holds credentials and host identity across disposable CLI containers.
Create `.env` using the WSL commands above, or copy `.env.example` to `.env`
(`cp` copies a file). For a Docker-only installation, copying needs no Python.

```bash
docker compose build
docker compose run --rm rosa-llm status --json
docker compose run --rm rosa-llm ask "Say hello" --json
```

`docker compose build` builds the image; `run --rm` runs one command and removes
its container afterward, preserving the named volume. Compose loads `.env`.
`DOCKER_OLLAMA_BASE_URL` overrides its Docker endpoint; the default is
`http://host.docker.internal:11434`. On native Linux this name maps to the host
gateway. The Ollama server must be reachable on that interface; a server listening
only on host loopback may need host networking instead. The package does not
change Ollama bindings, firewalls, or Docker settings.

For login on **native Linux Docker**, share the host network so the host browser
can reach the container's loopback listener:

```bash
docker compose -f compose.yaml -f compose.login.yaml run --rm rosa-llm login --no-browser
docker compose run --rm rosa-llm status --provider openai --json
docker compose run --rm rosa-llm test --provider openai --json
```

`-f` selects Compose files; later files override earlier ones. Open the printed
authorization URL in the browser on that host. No callback port is publicly
published, and the listener remains on loopback. Do not run simultaneous logins
on the same port. Use `--port` to choose another port if needed.

Docker Desktop host networking requires its supported host-network feature to
be explicitly enabled; this project does not enable it. If unavailable, perform
initial login using the native WSL package, then transfer **your own** credential
directory into your private volume using your organization's secure file-transfer
process, preserving owner-only permissions and container UID 10001. Move the host
identity with its runtime; do not operate concurrent copies as independent hosts.
Alternatively use a Linux Docker host and an existing SSH tunnel as above.
Bridge networking alone with a listener bound to container `127.0.0.1` does not
make host loopback callbacks work; publishing a port does not fix that bind.

All commands for one installation must use the same Compose project and volume.
Do not share a credential volume between team members. Do not use
`docker compose down -v` unless you intend to delete those credentials (`-v`
removes named volumes). Bind mounts must be pre-owned by UID 10001 and mode 700.
Native Windows Python is outside this release's scope; use WSL or Linux containers.

## Architecture, dependencies, and credential storage

```text
src/rosa_llm/
  config.py       .env / per-user / process configuration
  types.py        Message, Tool, ToolCall, Response, StreamEvent
  client.py       create_client(), shared API, safe connection errors
  langchain.py    optional ChatRosaLLM adapter for LangChain/ROSA
  providers.py    Ollama NDJSON and OpenAI Responses SSE adapters
  auth.py         OAuth, loopback callback, JWT validation, renewal/revocation
  storage.py      owner-only atomic files and Linux process locking
  http.py         safe HTTP error classification
  errors.py       public exception types
  cli.py          developer commands and smoke checks
```

Runtime dependencies are `httpx` for HTTP and streaming, `python-dotenv` for
configuration, and `PyJWT[crypto]` for RSA signature and claim validation.
`pytest` and `ruff` are development dependencies for tests and formatting/linting;
`uv_build` builds the installable package. The lock file includes transitive dependencies.

By default, `~/.config/rosa-llm/credentials.json` maps profile labels to separate
registrations and `host.json` persists a random host ID. `ROSA_LLM_AUTH_DIR`
overrides this location, which must be outside the project. Directories use mode
700; credential, host, temporary, and lock files use mode 600. Credential files
are plaintext protected by OS permissions, not encrypted at rest. Each successful
refresh atomically replaces the access token, rotating refresh token, scopes, and
expiry while holding a Linux process lock. A second process re-reads the new
record after obtaining the lock. Local filesystems are required; distributed/NFS
locking and multi-host credential sharing are outside scope.

Tokens are renewed near expiry. Revoked sessions require human login; temporary
network failures preserve credentials. No inference retries or provider fallbacks
are hidden in the client. HTTP redirects and environment proxy variables are
disabled on internally created HTTP clients; TLS verification remains enabled.
Do not enable HTTP wire/debug logging around OAuth because it can expose secrets.

## Automated checks and troubleshooting

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
```

`pytest -q` runs mocked tests with concise output; no live login or inference occurs.
Tests include signed JWTs, scope/state/nonce validation, rotation, multiprocess
refresh, storage permissions, provider streams/tool round trips, and CLI JSON.
Third-party pytest plugin auto-loading is disabled to isolate the suite from
ROS plugins present in sourced WSL shells. `ruff check` checks Python style and
common mistakes; `format --check` checks formatting without editing; `uv build`
creates a wheel and source distribution under `dist/`.

| Symptom | Action |
| --- | --- |
| Ollama connection refused in WSL | Check its actual host/port; Windows Ollama may need the WSL route gateway rather than localhost. Set `OLLAMA_BASE_URL` explicitly; there is no automatic endpoint switching. |
| Browser callback fails | Keep login running, check WSL forwarding or Docker host networking, and confirm the selected port is free. Use an existing SSH tunnel if needed. |
| Identity signed in, inference disabled | Reauthorize with `login --consent`; check account/workspace eligibility and granted plan permission. |
| Missing OpenAI model | Run `models --provider openai`; clear `OPENAI_MODEL` for catalog selection or use an available slug. |
| Usage limit/unavailable | Stop new requests and review [ChatGPT Usage](https://chatgpt.com/settings/usage); retry temporary unavailability later. The client does not bypass limits. |
| Token expired/revoked | Automatic refresh handles expiry; terminal refresh errors require `login` again with the same profile. |
| Credential permission error | Use a private directory owned by your OS user; files must be mode 600. Docker bind mounts must match UID 10001. |
| Partial stream followed by failure | Treat the request as failed; `generate()` never returns partial output as completed. |
| Unsupported feature | The native API supports text/history/function tools and OpenAI custom text tools. The optional LangChain adapter supports text/function tools. Hosted tools, images/files, native async HTTP clients and arbitrary OpenAI parameters are outside scope. |

The OpenAI implementation follows the official protocol, not Codex credentials
or unofficial ChatGPT endpoints. See [protocol sources](docs/protocol.md) and
[validation results and remaining manual checks](docs/validation.md).
