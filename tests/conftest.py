import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from rosa_llm.auth import ISSUER, SCOPES, OAuthManager
from rosa_llm.storage import CredentialStore


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    for key in (
        "LLM_PROVIDER",
        "OLLAMA_MODEL",
        "OLLAMA_BASE_URL",
        "OPENAI_MODEL",
        "ROSA_LLM_AUTH_DIR",
        "ROSA_LLM_PROFILE",
        "ROSA_LLM_TIMEOUT",
        "ROSA_LLM_REQUIRE_EXPLICIT_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def id_token(key, *, nonce="nonce", subject="user-1", client="oaiapp_test", **overrides):
    claims = {
        "iss": ISSUER,
        "aud": client,
        "sub": subject,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "nonce": nonce,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def tokens(**overrides):
    return {
        "access_token": "test-access",
        "refresh_token": "test-refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": SCOPES,
        **overrides,
    }


def record(**overrides):
    return {
        "client_id": "oaiapp_test",
        "subject": "user-1",
        "issuer": ISSUER,
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "id_token": "old-id",
        "scopes": SCOPES.split(),
        "expires_at": time.time() + 3600,
        **overrides,
    }


def save_record(store, value, profile="default"):
    with store.lock():
        data = store.read("credentials.json")
        data[profile] = value
        store.write("credentials.json", data)


@pytest.fixture
def auth_factory(tmp_path, signing_key):
    clients = []

    def factory(handler=None, profile="default"):
        def dispatch(request):
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(
                    200,
                    json={
                        "issuer": ISSUER,
                        "jwks_uri": ISSUER + "/.well-known/jwks.json",
                        "revocation_endpoint": ISSUER + "/revoke",
                    },
                )
            if request.url.path == "/.well-known/jwks.json":
                key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
                key.update({"kid": "test-key", "use": "sig"})
                return httpx.Response(200, json={"keys": [key]})
            if handler:
                return handler(request)
            return httpx.Response(200, json=tokens())

        http = httpx.Client(transport=httpx.MockTransport(dispatch))
        clients.append(http)
        return OAuthManager(CredentialStore(tmp_path / "auth"), http, profile)

    yield factory
    for http in clients:
        http.close()


def sse(*events):
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()


def completed(text="Hello", output=None):
    return {
        "type": "response.completed",
        "response": {
            "id": "resp_1",
            "model": "account-model",
            "status": "completed",
            "usage": {"input_tokens": 4, "output_tokens": 1},
            "output": output
            if output is not None
            else [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            ],
        },
    }


@pytest.fixture
def model_factory(auth_factory):
    # Optional dependencies are imported only when adapter tests request this fixture.
    pytest.importorskip("langchain_core", minversion="0.3.86")
    from collections import deque

    from rosa_llm import Config, create_client
    from rosa_llm.langchain import ChatRosaLLM

    clients = []

    def factory(responses=None, *, provider="openai", allowed_tools=None, handler=None):
        manager = auth_factory()
        save_record(manager.store, record())
        requests = []
        responses = deque(responses if responses is not None else [[completed()]])

        def dispatch(request):
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200, json={"models": [{"slug": "account-model", "visibility": "list"}]}
                )
            requests.append(request)
            if handler:
                return handler(request)
            events = responses.popleft()
            body = sse(*events) if provider == "openai" else "\n".join(map(json.dumps, events))
            return httpx.Response(200, content=body)

        http = httpx.Client(transport=httpx.MockTransport(dispatch))
        clients.append(http)
        client = create_client(
            Config(provider=provider, auth_dir=manager.store.directory), http=http
        )
        return ChatRosaLLM(client=client, allowed_tools=allowed_tools), requests

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def openai_client(auth_factory):
    from rosa_llm import Config, create_client

    clients = []

    def factory(events=None, *, handler=None, model="", catalog=None):
        seen = []
        manager = auth_factory()
        save_record(manager.store, record())

        def dispatch(request):
            seen.append(request)
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={
                        "models": catalog
                        if catalog is not None
                        else [
                            {"slug": "hidden", "visibility": "hide"},
                            {
                                "slug": "account-model",
                                "display_name": "Account Model",
                                "visibility": "list",
                            },
                            {"slug": "second-model", "visibility": "list"},
                        ]
                    },
                )
            if handler:
                return handler(request)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse(*(events if events is not None else [completed()])),
            )

        http = httpx.Client(transport=httpx.MockTransport(dispatch))
        clients.append(http)
        client = create_client(
            Config(provider="openai", auth_dir=manager.store.directory, openai_model=model),
            http=http,
        )
        return client, seen

    yield factory
    for client in clients:
        client.close()
