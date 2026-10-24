"""Developer CLI. JSON mode emits exactly one JSON document and never prompts."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx

from .auth import OAuthManager
from .client import create_client
from .config import Config, user_config_dir
from .errors import ConfigurationError, LLMError, ProviderError
from .http import transport_error
from .storage import CredentialStore
from .types import Message, Tool

TEMPLATE = """LLM_PROVIDER={provider}
OLLAMA_MODEL={ollama_model}
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_MODEL={openai_model}
ROSA_LLM_AUTH_DIR=
ROSA_LLM_PROFILE=default
ROSA_LLM_TIMEOUT=120
ROSA_LLM_REQUIRE_EXPLICIT_MODEL=false
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may embed untrusted argument values in its normal error text.
        raise ConfigurationError("Invalid CLI arguments; run rosa-llm <command> --help.")


def parser() -> argparse.ArgumentParser:
    root = Parser(description="Ollama and official ChatGPT OAuth, without ROSA dependencies.")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("init", "login", "logout", "status", "models", "ask", "test"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help="one JSON object on stdout")
        command.add_argument("--env-file", type=Path, help="local dotenv file (default: ./.env)")
        command.add_argument(
            "--provider", choices=("ollama", "openai"), help="override LLM_PROVIDER"
        )
        command.add_argument("--model", help="override model for the selected provider")
        command.add_argument("--profile", help="select a saved OpenAI registration")
        if name == "init":
            command.add_argument("--user", action="store_true", help="write per-user config.env")
        if name == "ask":
            command.add_argument("prompt")
            command.add_argument(
                "--stream", action="store_true", help="print text deltas as they arrive"
            )
        if name == "test":
            command.add_argument(
                "--tool", action="store_true", help="also test one harmless echo tool round trip"
            )
        if name == "login":
            command.add_argument(
                "--consent", action="store_true", help="explicitly request plan-use consent again"
            )
            command.add_argument(
                "--no-browser", action="store_true", help="print an authorization URL"
            )
            command.add_argument(
                "--port",
                type=int,
                default=1455,
                help="loopback callback port; 0 selects a free port",
            )
            command.add_argument(
                "--wait", type=float, default=180, help="callback wait limit in seconds"
            )
    return root


def emit(data: dict, json_mode: bool):
    if json_mode:
        print(json.dumps(data, ensure_ascii=False))
    elif "response" in data:
        print(data["response"])
        if data.get("tool_calls"):
            print(json.dumps({"tool_calls": data["tool_calls"]}, indent=2))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False))


def initialize(args) -> dict:
    if args.user and args.env_file:
        raise ConfigurationError("Choose --user or --env-file, not both.")
    destination = (
        user_config_dir() / "config.env" if args.user else (args.env_file or Path.cwd() / ".env")
    )
    provider = args.provider or "ollama"
    selected = Config(
        provider=provider,
        ollama_model=args.model if args.model and provider == "ollama" else "qwen3:8b",
        openai_model=args.model if args.model and provider == "openai" else "",
    )
    if args.model and any(char in args.model for char in "\r\n\x00"):
        raise ConfigurationError("Model name cannot contain control characters.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive create: initialization never overwrites someone's settings.
        with destination.open("x", encoding="utf-8") as stream:
            stream.write(
                TEMPLATE.format(
                    provider=provider,
                    ollama_model=selected.ollama_model,
                    openai_model=selected.openai_model,
                )
            )
    except FileExistsError:
        raise ConfigurationError(
            "Configuration already exists; edit it to change settings."
        ) from None
    return {"initialized": str(destination), "provider": provider, "model": selected.model or None}


def smoke_test(client, with_tool: bool):
    if not with_tool:
        response = client.generate("Reply with exactly ROSA_LLM_OK and no other text.")
        if response.text.strip() != "ROSA_LLM_OK" or response.tool_calls:
            raise ProviderError(
                "Live smoke request completed, but the expected short answer was not returned."
            )
    else:
        tools = [
            Tool(
                "echo",
                "Return the supplied value unchanged.",
                {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            )
        ]
        history = [
            Message(
                "user",
                "Call echo once with value ROSA_LLM_OK. "
                "After its result, reply exactly ROSA_LLM_OK.",
            )
        ]
        first = client.generate(history, tools=tools)
        if len(first.tool_calls) != 1:
            raise ProviderError("Live tool smoke request did not return exactly one tool call.")
        call = first.tool_calls[0]
        if (
            call.name != "echo"
            or call.kind != "function"
            or call.namespace not in {None, "local"}
            or call.arguments != {"value": "ROSA_LLM_OK"}
        ):
            raise ProviderError(
                "Live tool smoke returned an unexpected call; nothing was executed."
            )
        history += [
            first.message,
            Message("tool", "ROSA_LLM_OK", tool_call_id=call.id, tool_name="echo"),
        ]
        response = client.generate(history, tools=tools)
        if response.text.strip() != "ROSA_LLM_OK" or response.tool_calls:
            raise ProviderError("Live tool round trip did not produce the expected final answer.")
    response.metadata["smoke_test"] = "passed"
    response.metadata["tool_round_trip"] = with_tool
    return response


def run(args):
    if args.command == "init":
        emit(initialize(args), args.json)
        return
    config = Config.load(args.env_file)
    updates = {}
    if args.provider:
        updates["provider"] = args.provider
    if args.profile:
        updates["profile"] = args.profile
    if args.model:
        selected_provider = args.provider or config.provider
        if selected_provider == "ollama":
            updates["ollama_model"] = args.model
        else:
            updates["openai_model"] = args.model
    config = replace(config, **updates)
    if args.command in {"login", "logout", "status"}:
        data = {"provider": config.provider, "model": config.model or None}
        # Ollama status does not require or create an OpenAI credential directory.
        if args.command == "status" and config.provider == "ollama":
            data.update({"authentication_required": False, "network_checked": False})
        else:
            with httpx.Client(
                timeout=config.timeout, follow_redirects=False, trust_env=False
            ) as http:
                auth = OAuthManager(CredentialStore(config.credential_dir), http, config.profile)
                if args.command == "login":
                    if not 0 <= args.port <= 65535 or not 0 < args.wait <= 300:
                        raise ConfigurationError(
                            "Login port must be 0-65535; wait must be 1-300 seconds."
                        )
                    auth.login(
                        port=args.port,
                        open_browser=not args.no_browser,
                        consent=args.consent,
                        timeout=args.wait,
                        announce=lambda text: print(text, file=sys.stderr),
                    )
                    data.update(auth.status())
                elif args.command == "logout":
                    data.update(auth.logout())
                    if not data["remote_revocation_confirmed"]:
                        print(
                            "Remote revocation was not confirmed; "
                            "disconnect rosa-llm in ChatGPT Settings.",
                            file=sys.stderr,
                        )
                else:
                    data.update(auth.status())
        emit(data, args.json)
        return
    with create_client(config) as client:
        if args.command == "models":
            emit({"provider": config.provider, "models": client.models()}, args.json)
        elif args.command == "test":
            emit(smoke_test(client, args.tool).to_dict(), args.json)
        elif args.stream:
            if args.json:
                raise ConfigurationError(
                    "Use --json or --stream; JSON output is buffered until completion."
                )
            printed_text = ""
            for event in client.stream(args.prompt):
                if event.type == "text":
                    print(event.text, end="", flush=True)
                    printed_text += event.text
                elif event.response:
                    print(event.response.text[len(printed_text) :], end="", flush=True)
                    if event.response.tool_calls:
                        print(json.dumps(event.response.to_dict()["tool_calls"]))
            print()
        else:
            emit(client.generate(args.prompt).to_dict(), args.json)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    json_mode = "--json" in argv
    diagnostic = None
    try:
        run(parser().parse_args(argv))
        return 0
    except LLMError as error:
        diagnostic = error.to_dict()
        code, message = error.code, str(error)
    except httpx.HTTPError as error:
        diagnostic = transport_error(error).to_dict()
        code, message = "network_error", "Cannot reach authentication service; check your network."
        diagnostic.update(code=code, message=message)
    except OSError:
        code, message = (
            "local_io_error",
            "Local file or callback listener failed; check permissions and port availability.",
        )
    except KeyboardInterrupt:
        code, message = "interrupted", "Operation interrupted."
    if json_mode:
        print(json.dumps({"error": diagnostic or {"code": code, "message": message}}))
    print(message, file=sys.stderr)
    return 1
