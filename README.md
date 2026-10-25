# rosa-llm

**OpenAI through ChatGPT OAuth and local Ollama, behind one Python interface.**

Use it as a standalone client or supply `ChatRosaLLM` to LangChain and ROSA.
The package handles model communication; your application executes tools and
owns its agent logic, experiment design, and outcome checks.

**v0.2.0** · **Python ≥3.11** · **[Apache-2.0](LICENSE)**

[Install](#install) · [Provider setup](#provider-setup) · [Python API](#python-api) ·
[LangChain and ROSA](#langchain-and-rosa) · [Docker](#docker) ·
[Configuration](#configuration) · [Troubleshooting](#troubleshooting)

| Use case | What you need |
| --- | --- |
| Standalone client | Python and a configured provider; ROS and LangChain are optional |
| OpenAI | Your own ChatGPT account with eligible model access and plan-use permission; no OpenAI API key |
| Ollama | A reachable server and an already installed model |
| LangChain / ROSA | The `langchain` extra and your existing agent/ROS environment |

## Install

### In an existing uv project

For the base client:

```bash
uv add "rosa-llm @ git+https://github.com/Lelevinson/rosa-llm-package.git@v0.2.0"
```

For LangChain or ROSA, use this dependency instead:

```bash
uv add "rosa-llm[langchain] @ git+https://github.com/Lelevinson/rosa-llm-package.git@v0.2.0"
```

`uv add` updates the consuming project's dependencies and lockfile. Public HTTPS
installation needs no GitHub sign-in. If your project already declares this
dependency, use its `uv sync --locked`; a separate package clone is unnecessary.

> **Python compatibility:** the base package and adapter require ≥3.11 and have
> been tested on 3.11 and 3.12 under Linux/WSL x86_64. This checkout and Docker use 3.12.
> Use Python 3.12 for the installed ROS 2 Jazzy bindings; the package does not
> install ROS. See [verified versions and limits](docs/python-compatibility.md).

<details>
<summary>Pinning a research dependency or keeping a Python ≥3.9 project</summary>

The published v0.2.0 commit is `0b15070f57b9b34790f7fb4c1330a0b19bf69f98`.
A tag installation records its resolved commit in `uv.lock`; use the full SHA
in the Git URL when an immutable source reference is required.

uv checks the consumer's declared Python range, not just its active interpreter.
For a project that retains ≥3.9 support, a conditional dependency can omit this
adapter on Python 3.9/3.10:

```bash
uv add --marker "python_version >= '3.11'" \
  "rosa-llm[langchain] @ git+https://github.com/Lelevinson/rosa-llm-package.git@0b15070f57b9b34790f7fb4c1330a0b19bf69f98"
```

`--marker` makes the dependency conditional. Older interpreters will not receive
the adapter. Existing ROS bindings and dependency groups may require a newer
Python version independently. Lowering this package's minimum alone does not
resolve every consumer's compatibility constraints.

</details>

### Run the CLI from a checkout

With Git, Python 3.12, and [uv](https://docs.astral.sh/uv/getting-started/installation/)
available, run in your Linux or WSL terminal:

```bash
git clone https://github.com/Lelevinson/rosa-llm-package.git
cd rosa-llm-package
uv sync --locked
uv run rosa-llm init
```

`git clone` downloads the project; `cd` enters it. `uv sync --locked` installs the
locked environment in `.venv`; `uv run` executes a command there. `init` creates
`.env` and refuses to overwrite it. Skip `init` if you already have configuration.
See [uv's sync behavior](https://docs.astral.sh/uv/concepts/projects/sync/).

## Provider setup

Choose a provider below. `YOUR_OLLAMA_MODEL` and `YOUR_OPENAI_MODEL` are
placeholders: replace them with names returned by the relevant catalog command.

### Ollama

**1. Configure the server address** in your local `.env`:

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

In WSL, a Windows-hosted server may need the Windows host address instead.
Use the address reachable from your own machine; the client does not switch
endpoints, start servers, or download models.

**2. Discover a model and make a request:**

```bash
uv run rosa-llm models --provider ollama --json
uv run rosa-llm ask "Say hello" --provider ollama --model YOUR_OLLAMA_MODEL --json
```

Save that model as `OLLAMA_MODEL` in `.env` to avoid repeating `--model`.
Tool use requires a tool-capable model.

### OpenAI — Sign in with ChatGPT

**1. Authorize your own account:**

```bash
uv run rosa-llm login --provider openai
uv run rosa-llm status --provider openai --json
```

Login opens a browser and listens on `127.0.0.1:1455/auth/callback`.
Identity login and plan-use permission are separate; inference needs both.
`status` reads local state and does not confirm current remote access.

**2. Discover a model, then request it explicitly:**

```bash
ROSA_LLM_REQUIRE_EXPLICIT_MODEL=false uv run rosa-llm models --provider openai --json
uv run rosa-llm ask "Say hello" --provider openai --model YOUR_OPENAI_MODEL --json
```

The assignment before `models` disables the explicit-model guard for that command
only, allowing discovery before a model is configured. It does not change `.env`.
`--provider` and `--model` override configuration for one command.

**3. Save your selection** in `.env`:

```dotenv
LLM_PROVIDER=openai
OPENAI_MODEL=YOUR_OPENAI_MODEL
ROSA_LLM_REQUIRE_EXPLICIT_MODEL=true
```

Credentials are saved outside the repository. Each teammate signs in separately;
GitHub access does not provide model access. An OpenAI API key is not used here.

<details>
<summary>Browser forwarding, profiles, consent, and logout</summary>

- **No browser window?** Run `uv run rosa-llm login --no-browser` and open the
  printed authorization URL on the browser host while login remains running.
  Keep the URL private; do not paste callback URLs into chat.
- **WSL:** Windows-to-WSL localhost forwarding must reach the listener. Keep the
  callback host as `127.0.0.1`; do not replace it with `localhost` or expose it
  publicly. `--port 0` selects a free loopback port.
- **Existing SSH access:** on the browser host, run
  `ssh -N -L 1455:127.0.0.1:1455 user@your-linux-host`, then run login on that
  Linux host with `--no-browser --port 1455`. `-N` runs only forwarding; `-L`
  forwards the local port to the remote loopback listener. SSH setup is external.
- **Separate accounts/workspaces:** use `--profile lab` on login and subsequent
  commands, or set `ROSA_LLM_PROFILE=lab`. Labels accept letters, digits, `_`,
  and `-`. Returning logins reuse the registration and verify its subject.
- **Missing plan permission:** `uv run rosa-llm login --consent` requests consent
  again. A new identity-only registration retains inference-disabled state;
  failed login does not replace an existing valid session.
- **Sign out:** `uv run rosa-llm logout --json` attempts remote revocation and
  clears the selected profile's tokens, retaining its registration and host ID.
  If revocation cannot be confirmed, disconnect the app in ChatGPT Settings.

</details>

## Python API

The base API needs no ROS, LangChain, database, or agent runtime:

```python
from rosa_llm import create_client

with create_client() as client:
    response = client.generate("Explain a ROS 2 node briefly.")
    print(response.text)
    print(response.model, response.usage)
```

`create_client()` loads configuration from the calling directory. For explicit
settings, pass `Config(...)`, or use `create_client(env_file="path/to/.env")`.
Keep client use inside `with` so its HTTP resources close afterward.

| Interface | Contract |
| --- | --- |
| `client.generate(prompt)` | Returns a validated completed `Response`; partial output is not returned as success |
| `client.generate(messages)` | Uses explicit history; retain `response.message` to preserve native reasoning and tool-call items |
| `client.stream(prompt)` | Yields text events and a final completed response; exhaust or close the iterator and handle `LLMError` |
| `Tool(...)` / `response.tool_calls` | Declares tools and receives requests; your code validates arguments and executes them |
| `create_client(observer=callback)` | Captures request records; the application owns thread-safe, durable storage and task scoring |

Plain text history is portable across providers; opaque provider history is not.
OpenAI maps system instructions to developer messages. Ollama accepts system
messages and rejects developer messages. Tool arguments are complete only after
provider completion; do not execute partial streamed arguments.

Recipes: [chat/history](examples/chat.py) · [native tool loop](examples/tool_call.py) ·
[request recording and timing](docs/experiment-recording.md).

## LangChain and ROSA

Install with `[langchain]` as shown above. In this checkout, enable the extra with
`uv sync --locked --extra langchain`; it pins `langchain-core==0.3.86`.

```python
from rosa_llm import create_client
from rosa_llm.langchain import ChatRosaLLM

with create_client() as client:
    model = ChatRosaLLM(client=client)
    print(model.invoke("Say hello").content)
```

Use ordinary LangChain tools through `model.bind_tools(...)` or an agent's normal
model interface. LangChain/your application executes tools; this adapter does not.
Keep the client open until all agent calls finish.

<details>
<summary>Pass the same adapter to an existing ROSA application</summary>

Install ROSA separately and source your ROS environment first. The package's
Docker image does not supply ROS. With those dependencies available:

```python
from rosa import ROSA
from rosa_llm import create_client
from rosa_llm.langchain import ChatRosaLLM

with create_client() as client:
    model = ChatRosaLLM(client=client)
    agent = ROSA(ros_version=2, llm=model, streaming=False)
    print(agent.invoke("List the current ROS 2 nodes."))
```

The model adapter is infrastructure. Your research code still owns policies,
tool permissions, observations, experimental conditions, and outcome validation.

</details>

| Capability | OpenAI ChatGPT OAuth | Ollama |
| --- | --- | --- |
| Text/history and streamed text | Supported | Supported |
| JSON function tools | Supported; native API wraps tools in namespaces | Supported; JSON-object arguments |
| Custom text tools | Native API only | Unsupported |
| LangChain tools | Text/function-tool contract | Text/function-tool contract |
| Per-call options | `tool_choice` (`auto`, `none`, `required`), `parallel_tool_calls` | `options`, `think`, `format`, `keep_alive` |
| Async | LangChain worker-thread interface; base client uses synchronous HTTP | Same |

Unsupported options fail explicitly. Do not assume temperature/seed controls,
strict schemas, hosted tools, images/files, or arbitrary OpenAI parameters are
portable across providers. There are no constructor-level decoding defaults on
`ChatRosaLLM` in v0.2.0; supplying per-call options and retaining them through an
agent's tool-binding step is the caller's responsibility.

See [integration examples](docs/langchain-integration.md),
[custom LangChain tools](examples/langchain_tools.py), and the
[compatibility report](docs/langchain-compatibility.md) for exact supported scope.

## Docker

**Status:** Dockerfiles/Compose configuration are provided. Image build, runtime,
and credential persistence have not been live-verified in this environment.
See [verification limits](docs/hardening-report.md).

The image is a standalone Python 3.12 client, not a complete ROS/Jazzy environment.
For an existing ROS container, install the Git dependency into that application's
environment. This image defaults to the base client; build with
`--build-arg INSTALL_LANGCHAIN=true` to include the optional adapter.

Create a local `.env` from `.env.example` if one does not already exist, then:

```bash
docker compose build
docker compose run --rm rosa-llm models --provider ollama --json
docker compose run --rm rosa-llm ask "Say hello" --provider ollama --model YOUR_OLLAMA_MODEL --json
```

`build` creates the image. `run --rm` removes the command container when it exits;
the named credential volume persists. Compose loads `.env`.

| Setting | Container behavior |
| --- | --- |
| Ollama endpoint | `DOCKER_OLLAMA_BASE_URL`, default `http://host.docker.internal:11434`; server must listen on a reachable interface |
| Credential directory | `/home/app/.config/rosa-llm`, persisted in the `auth` named volume |
| Runtime user | Non-root UID 10001; bind-mounted credential directories must be owned by that UID and mode 700 |

### OAuth login in Docker

On native Linux, or Docker Desktop with [host networking enabled](https://docs.docker.com/engine/network/drivers/host/):

```bash
docker compose -f compose.yaml -f compose.login.yaml run --rm rosa-llm login --no-browser
docker compose run --rm -e ROSA_LLM_REQUIRE_EXPLICIT_MODEL=false rosa-llm models --provider openai --json
docker compose run --rm rosa-llm ask "Say hello" --provider openai --model YOUR_OPENAI_MODEL --json
```

`-f` selects Compose files; the later file overrides the earlier one. `-e` sets
an environment variable inside that command's container. Open the printed URL in the browser on that host while login waits. The listener remains
on loopback. Bridge networking and publishing a port do not make a listener bound
to the container's `127.0.0.1` reachable from the host. The project does not enable
host networking or change host firewalls/Ollama bindings for you.

If host networking is unavailable, use an existing SSH tunnel, or complete WSL
login and securely move **your own** credential/host-identity directory into your
private volume with the correct ownership. Move the runtime identity together;
do not operate concurrent independent copies. Never share credential volumes
between teammates. Use the same Compose project/volume across commands, and avoid
`docker compose down -v` unless credential deletion is intentional (`-v` deletes
named volumes). Native Windows Python is outside this release's tested scope.

## Configuration

Edit the `.env` in the directory from which your application runs. No tokens or
API keys belong in this file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `ollama` | `ollama` or `openai` |
| `OLLAMA_MODEL` | `qwen3:8b` | Installed model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Endpoint reachable from this process |
| `OPENAI_MODEL` | Empty | Account-visible model name; empty otherwise selects the first visible catalog entry on each request |
| `ROSA_LLM_REQUIRE_EXPLICIT_MODEL` | `false` | Set `true` for controlled OpenAI runs to reject missing model selection |
| `ROSA_LLM_PROFILE` | `default` | Saved OAuth registration label |
| `ROSA_LLM_AUTH_DIR` | User configuration directory | Credential/host storage outside the repository; empty uses the default |
| `ROSA_LLM_TIMEOUT` | `120` | HTTP phase timeout in seconds, not a total trial deadline |

**Precedence, highest first:** process environment → local `.env` → user
`config.env` → defaults. The user directory is `$XDG_CONFIG_HOME/rosa-llm`, normally
`~/.config/rosa-llm`. `uv run rosa-llm init --user` creates its `config.env`.
The loader does not change process environment or expand `${...}` in dotenv values.
For reproducible runs, select a named model rather than relying on catalog order.

## CLI reference

All options follow the subcommand; for example, `uv run rosa-llm ask --help`.

| Command | What it does |
| --- | --- |
| `init` | Creates configuration without overwriting an existing file |
| `login` / `logout` | Signs in / clears the selected profile's tokens, attempting remote revocation |
| `status --json` | Reads local authentication state; does not contact OpenAI |
| `models --json` | Queries the selected provider's model catalog |
| `ask "prompt" --json` | Performs real inference; JSON includes provider/model, text, tool calls, usage, and available metadata |
| `ask "prompt" --stream` | Prints text deltas; incompatible with `--json` |
| `test --json` | Makes one real request and checks an exact short marker |
| `test --tool --json` | Checks one harmless echo tool round trip, arguments, and final marker; at most two model requests |

Common overrides: `--provider`, `--model`, `--profile`, `--env-file PATH`.
Diagnostics go to stderr. JSON-mode failures emit an `error` object and exit 1.
Inference never opens a login prompt or automatically retries/falls back.
Partial streaming output is not proof of success; check the exit status.

## Credentials and transport

- Default storage: `~/.config/rosa-llm/credentials.json` and `host.json`.
  Profiles belong to this package; it does not reuse Codex credentials.
  Files are plaintext protected by OS permissions, not encrypted.
- Storage directories use mode 700; credential, host, temporary, and lock files
  use mode 600. Refreshes atomically rotate tokens under a Linux process lock.
  Local filesystems are required; NFS/distributed locking is outside scope.
- Expired tokens renew automatically when possible. Revoked sessions need login
  again; temporary network failures preserve credentials. Keep credentials and
  authorization URLs private. Never put GitHub/model tokens in Git URLs or logs.
- Redirects and environment proxy variables are disabled on internally created
  HTTP clients; TLS verification stays enabled. Do not enable HTTP wire/debug
  logging around OAuth, because it can expose secrets.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Ollama connection refused | Verify the server, installed model, and endpoint from this WSL/container; localhost may address the wrong machine |
| Browser callback fails | Keep login running; check WSL forwarding or Docker host networking and port conflicts |
| Logged in, inference disabled | Check plan eligibility/permission; use `login --consent` to request permission again |
| OpenAI model unavailable | Refresh the catalog using the command in [OpenAI setup](#openai--sign-in-with-chatgpt); select a listed model |
| Explicit-model error during discovery | Prefix the catalog command with `ROSA_LLM_REQUIRE_EXPLICIT_MODEL=false`; keep inference guard enabled afterward |
| Usage limit or provider unavailable | Review [ChatGPT Usage](https://chatgpt.com/settings/usage); stop or defer requests rather than bypassing limits |
| Credential permission error | Check user ownership, file mode 600, directory mode 700, and Docker UID 10001 |
| Stream ends with an error | Record failure even if text was already printed; `generate()` does not return that partial result as completed |
| Unsupported option/tool/message | Check the [compatibility contract](docs/langchain-compatibility.md); capabilities differ by provider |

## Development and reference

```bash
uv sync --locked --extra langchain
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build
```

`pytest -q` runs automated tests with concise output; it does not authorize accounts
or run live model requests. Third-party pytest plugin auto-loading is disabled.
Optional ROSA executor checks also need the `integration` group and sourced Jazzy;
see the [integration guide](docs/langchain-integration.md).
`ruff check` checks code; `format --check` checks formatting without editing.
`uv build` creates a wheel and source distribution under `dist/`.

| Reference | Contents |
| --- | --- |
| [LangChain / ROSA](docs/langchain-integration.md) | Optional installation, agent examples, ROS environment requirements |
| [Compatibility](docs/langchain-compatibility.md) | Tool/message contracts, supported interfaces, provider restrictions |
| [Experiment recording](docs/experiment-recording.md) | Native records, timing definitions, provenance, runner responsibilities |
| [Python support](docs/python-compatibility.md) | Tested versions, wheel checks, dependency-resolution boundaries |
| [Protocol](docs/protocol.md) | Official OAuth/protocol sources |
| [Hardening](docs/hardening-report.md) / [validation](docs/validation.md) | Dated evidence and remaining manual checks |

<details>
<summary>Source layout</summary>

| Module | Responsibility |
| --- | --- |
| `client.py`, `types.py` | Shared client API and message/tool/response types |
| `providers.py`, `http.py` | Provider wire formats, stream parsing, safe error classification |
| `auth.py`, `storage.py` | OAuth, token validation/refresh, protected credential storage |
| `config.py`, `cli.py` | Configuration precedence and command-line interface |
| `langchain.py`, `provenance.py` | Optional model adapter and package/source identity |

Runtime dependencies are `httpx`, `python-dotenv`, and `PyJWT[crypto]`.
Development uses pytest and Ruff; `uv_build` builds the package.

</details>
