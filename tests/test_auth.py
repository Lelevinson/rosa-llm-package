import base64
import hashlib
import multiprocessing
import os
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from rosa_llm import AuthenticationError, ConfigurationError, PermissionDenied
from rosa_llm.auth import RESOURCE, OAuthManager, PendingLogin, parse_callback
from rosa_llm.storage import CredentialStore

from .conftest import id_token, record, save_record, tokens


def pending(client="dynamic_agent_client"):
    return PendingLogin(client, "http://127.0.0.1:1455/auth/callback", "urn:uuid:host")


def callback(attempt, **overrides):
    return urlencode(
        {"state": attempt.state, "code": "private-code", "client_id": "oaiapp_test", **overrides}
    )


def test_pkce_and_authorization_parameters():
    first, second = pending(), pending()
    values = parse_qs(urlsplit(first.url()).query)
    assert first.state != second.state and first.nonce != second.nonce
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(first.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert values["code_challenge"] == [expected]
    assert values["code_challenge_method"] == ["S256"]
    assert values["resource"] == [RESOURCE]
    assert values["agent_name_hint"] == ["rosa-llm"]
    assert values["redirect_uri"] == [first.redirect_uri]
    assert len(first.verifier) >= 43


def test_returning_login_hints_never_in_manual_url():
    attempt = pending("oaiapp_test")
    assert "agent_name_hint" not in attempt.url(record(), hints=True)
    assert "id_token_hint" in attempt.url(record(), hints=True)
    assert "old-id" not in attempt.url(record())


@pytest.mark.parametrize(
    "change,exception",
    [
        ({"state": "wrong"}, AuthenticationError),
        ({"error": "access_denied"}, PermissionDenied),
        ({"code": ""}, AuthenticationError),
        ({"client_id": "dynamic_agent_client"}, AuthenticationError),
    ],
)
def test_callback_rejections(change, exception):
    attempt = pending()
    with pytest.raises(exception):
        parse_callback(callback(attempt, **change), attempt)


def test_duplicate_replayed_expired_callbacks():
    attempt = pending()
    with pytest.raises(AuthenticationError):
        parse_callback(callback(attempt) + "&state=extra", attempt)
    attempt = pending()
    parse_callback(callback(attempt), attempt)
    with pytest.raises(AuthenticationError):
        parse_callback(callback(attempt), attempt)
    attempt = pending()
    attempt.created_at -= 301
    with pytest.raises(AuthenticationError):
        parse_callback(callback(attempt), attempt)


def test_returning_client_id_cannot_change():
    attempt = pending("oaiapp_original")
    with pytest.raises(AuthenticationError, match="client ID"):
        parse_callback(callback(attempt), attempt)


def test_successful_login_persistence_permissions(auth_factory, signing_key):
    attempt = pending()
    seen = []

    def handler(request):
        seen.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=tokens(id_token=id_token(signing_key, nonce=attempt.nonce)))

    manager = auth_factory(handler)
    manager.finish_login(attempt, callback(attempt))
    assert manager.access_token() == "test-access"
    assert manager.status()["plan_permission"] is True
    assert seen[0]["client_id"] == ["oaiapp_test"]
    assert seen[0]["code_verifier"] == [attempt.verifier]
    assert seen[0]["redirect_uri"] == [attempt.redirect_uri]
    assert "client_secret" not in seen[0]
    assert manager.store.directory.stat().st_mode & 0o777 == 0o700
    assert (manager.store.directory / "credentials.json").stat().st_mode & 0o777 == 0o600
    second = auth_factory()
    assert second.access_token() == "test-access"


@pytest.mark.parametrize(
    "overrides",
    [
        {"nonce": "wrong"},
        {"iss": "https://evil.example"},
        {"aud": "other-client"},
        {"exp": int(time.time()) - 60},
        {"sub": ""},
    ],
)
def test_id_token_validation(auth_factory, signing_key, overrides):
    manager = auth_factory()
    token = id_token(signing_key, **overrides)
    with pytest.raises(AuthenticationError):
        manager.validate_identity(token, "oaiapp_test", "nonce")


def test_bad_signature_and_unsigned_token(auth_factory):
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        auth_factory().validate_identity(id_token(other), "oaiapp_test", "nonce")
    with pytest.raises(AuthenticationError):
        auth_factory().validate_identity("not.a.jwt", "oaiapp_test", "nonce")


@pytest.mark.parametrize("scope", ["openid email", "openid resource.invoke"])
def test_missing_permission_does_not_replace_session(auth_factory, signing_key, scope):
    attempt = pending("oaiapp_test")
    manager = auth_factory(
        lambda request: httpx.Response(
            200, json=tokens(id_token=id_token(signing_key, nonce=attempt.nonce), scope=scope)
        )
    )
    old = record()
    save_record(manager.store, old)
    with pytest.raises(PermissionDenied):
        manager.finish_login(attempt, callback(attempt), old)
    with manager.store.lock():
        assert manager._record() == old


def test_returning_identity_must_match(auth_factory, signing_key):
    attempt = pending("oaiapp_test")
    manager = auth_factory(
        lambda request: httpx.Response(
            200,
            json=tokens(
                id_token=id_token(signing_key, nonce=attempt.nonce, subject="different-user")
            ),
        )
    )
    old = record()
    save_record(manager.store, old)
    with pytest.raises(AuthenticationError, match="identity differs"):
        manager.finish_login(attempt, callback(attempt), old)
    assert manager.access_token() == old["access_token"]


def test_refresh_rotates_and_keeps_profile_mapping(auth_factory):
    requests = []

    def handler(request):
        requests.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=tokens(refresh_token="replacement"))

    manager = auth_factory(handler)
    save_record(manager.store, record(expires_at=0))
    assert manager.access_token() == "test-access"
    assert manager.access_token() == "test-access"
    assert len(requests) == 1
    assert requests[0]["refresh_token"] == ["old-refresh"]
    assert requests[0]["resource"] == [RESOURCE]
    assert "scope" not in requests[0]
    with manager.store.lock():
        assert manager._record()["refresh_token"] == "replacement"
        assert manager._record()["client_id"] == "oaiapp_test"


def test_revoked_session_requires_human_and_preserves_registration(auth_factory):
    manager = auth_factory(lambda request: httpx.Response(400, json={"error": "invalid_grant"}))
    save_record(manager.store, record(expires_at=0))
    with pytest.raises(AuthenticationError):
        manager.access_token()
    assert manager.status()["authenticated"] is False
    with manager.store.lock():
        data = manager._record()
        assert data["client_id"] == "oaiapp_test"
        assert "refresh_token" not in data


def test_network_failure_keeps_tokens(auth_factory):
    def handler(request):
        raise httpx.ConnectError("sensitive-url", request=request)

    manager = auth_factory(handler)
    original = record(expires_at=0)
    save_record(manager.store, original)
    with pytest.raises(httpx.ConnectError):
        manager.access_token()
    with manager.store.lock():
        assert manager._record() == original


def test_missing_login(auth_factory):
    with pytest.raises(AuthenticationError, match="Human authentication"):
        auth_factory().access_token()


def test_logout_revocation_and_profile_isolation(auth_factory):
    forms = []

    def handler(request):
        forms.append(parse_qs(request.content.decode()))
        return httpx.Response(200)

    manager = auth_factory(handler)
    save_record(manager.store, record())
    save_record(manager.store, record(access_token="another-user"), "second")
    host = manager.store.host_id()
    assert manager.logout()["remote_revocation_confirmed"] is True
    assert forms[0]["token_type_hint"] == ["refresh_token"]
    with manager.store.lock():
        data = manager.store.read("credentials.json")
        assert "refresh_token" not in data["default"]
        assert data["second"]["access_token"] == "another-user"
        assert data["default"]["client_id"] == "oaiapp_test"
    assert manager.store.host_id() == host


def test_failed_remote_logout_clears_locally(auth_factory):
    manager = auth_factory(lambda request: httpx.Response(503))
    save_record(manager.store, record())
    assert manager.logout()["remote_revocation_confirmed"] is False
    assert manager.status()["authenticated"] is False


def test_storage_rejects_project_and_world_readable_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("")
    with pytest.raises(ConfigurationError):
        CredentialStore(tmp_path / "credentials")
    store = CredentialStore(tmp_path.parent / (tmp_path.name + "-outside"))
    with store.lock():
        store.write("credentials.json", {"safe": "value"})
        (store.directory / "credentials.json").chmod(0o644)
        with pytest.raises(AuthenticationError):
            store.read("credentials.json")


def test_symlinks_rejected(tmp_path):
    destination = tmp_path / "real"
    destination.mkdir()
    link = tmp_path / "link"
    link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(AuthenticationError):
        CredentialStore(link).host_id()


def _refresh_worker(directory, counter, ready, results):
    def dispatch(request):
        with counter.get_lock():
            counter.value += 1
        time.sleep(0.1)
        return httpx.Response(
            200, json=tokens(access_token="one-access", refresh_token="one-refresh")
        )

    try:
        with httpx.Client(transport=httpx.MockTransport(dispatch)) as http:
            auth = OAuthManager(CredentialStore(directory), http)
            ready.wait(5)
            results.put(auth.access_token())
    except Exception as error:
        results.put(type(error).__name__)


def test_concurrent_processes_refresh_only_once(tmp_path):
    # LangChain can start worker threads before this test; use fresh processes.
    context = multiprocessing.get_context("spawn")
    store = CredentialStore(tmp_path / "concurrent")
    save_record(store, record(expires_at=0))
    counter = context.Value("i", 0)
    ready, results = context.Event(), context.Queue()
    processes = [
        context.Process(target=_refresh_worker, args=(store.directory, counter, ready, results))
        for _ in range(3)
    ]
    for process in processes:
        process.start()
    try:
        ready.set()
        assert [results.get(timeout=10) for _ in processes] == ["one-access"] * 3
        assert counter.value == 1
    finally:
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join()
    assert all(process.exitcode == 0 for process in processes)


def test_host_persists(tmp_path):
    first = CredentialStore(tmp_path / "persistent")
    host = first.host_id()
    assert host.startswith("urn:uuid:")
    assert CredentialStore(first.directory).host_id() == host
    assert os.stat(first.directory / "host.json").st_mode & 0o777 == 0o600


def test_identity_only_login_is_retained_but_cannot_infer(auth_factory, signing_key):
    attempt = pending()
    manager = auth_factory(
        lambda request: httpx.Response(
            200,
            json={
                "id_token": id_token(signing_key, nonce=attempt.nonce),
                "scope": "openid email profile",
            },
        )
    )
    with pytest.raises(PermissionDenied, match="plan inference was not granted"):
        manager.finish_login(attempt, callback(attempt))
    status = manager.status()
    assert status["identity_authenticated"] and not status["plan_permission"]
    with pytest.raises(PermissionDenied):
        manager.access_token()
    with manager.store.lock():
        assert manager._record()["client_id"] == "oaiapp_test"


@pytest.mark.parametrize(
    "code", ["refresh_token_expired", "refresh_token_reused", "invalid_refresh_token"]
)
def test_terminal_refresh_errors_clear_unusable_tokens(auth_factory, code):
    manager = auth_factory(lambda request: httpx.Response(400, json={"error": code}))
    save_record(manager.store, record(expires_at=0))
    with pytest.raises(AuthenticationError):
        manager.access_token()
    assert not manager.status()["authenticated"]


def test_bad_rotation_response_requires_reauthorization(auth_factory):
    manager = auth_factory(lambda request: httpx.Response(200, json=tokens(refresh_token="")))
    save_record(manager.store, record(expires_at=0))
    with pytest.raises(AuthenticationError):
        manager.access_token()
    assert not manager.status()["authenticated"]


def test_atomic_write_failure_preserves_old_record(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "atomic")
    save_record(store, record())

    def fail_replace(*args):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr("rosa_llm.storage.os.replace", fail_replace)
    with store.lock():
        old = store.read("credentials.json")
        with pytest.raises(OSError):
            store.write("credentials.json", {"changed": True})
        assert store.read("credentials.json") == old
        assert not list(store.directory.glob(".pending-*"))


def test_new_login_does_not_clobber_concurrent_profile_change(auth_factory, signing_key):
    attempt = pending("oaiapp_test")
    manager = auth_factory(
        lambda request: httpx.Response(
            200, json=tokens(id_token=id_token(signing_key, nonce=attempt.nonce))
        )
    )
    previous = record()
    save_record(manager.store, {**previous, "access_token": "concurrent-refresh"})
    with pytest.raises(AuthenticationError, match="Profile changed"):
        manager.finish_login(attempt, callback(attempt), previous)
    assert manager.access_token() == "concurrent-refresh"


def test_explicit_consent_only_when_requested():
    attempt = pending("oaiapp_test")
    assert "prompt" not in parse_qs(urlsplit(attempt.url()).query)
    assert parse_qs(urlsplit(attempt.url(consent=True)).query)["prompt"] == ["consent"]


def test_refresh_rotation_survives_key_server_outage(auth_factory, signing_key, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=tokens(
                access_token="rotated-access",
                refresh_token="rotated-refresh",
                id_token=id_token(signing_key),
            ),
        )

    manager = auth_factory(handler)
    save_record(manager.store, record(expires_at=0))
    actual_validate = manager.validate_identity

    def offline(*args, **kwargs):
        raise httpx.ConnectError("test key-server outage")

    monkeypatch.setattr(manager, "validate_identity", offline)
    with pytest.raises(httpx.ConnectError):
        manager.access_token()
    with manager.store.lock():
        data = manager._record()
        assert data["refresh_token"] == "rotated-refresh"
        assert data["pending_id_token"]
    monkeypatch.setattr(manager, "validate_identity", actual_validate)
    assert manager.access_token() == "rotated-access"
    assert len(seen) == 1
    with manager.store.lock():
        assert "pending_id_token" not in manager._record()


def test_refresh_new_identity_cannot_replace_existing_user(auth_factory, signing_key):
    manager = auth_factory(
        lambda request: httpx.Response(
            200, json=tokens(id_token=id_token(signing_key, subject="another-user"))
        )
    )
    save_record(manager.store, record(expires_at=0))
    with pytest.raises(AuthenticationError, match="identity changed"):
        manager.access_token()
    assert not manager.status()["authenticated"]


def test_credentials_cannot_be_stored_in_another_git_repo(tmp_path):
    repository = tmp_path / "other-repo"
    (repository / ".git").mkdir(parents=True)
    (repository / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    with pytest.raises(ConfigurationError, match="Git repository"):
        CredentialStore(repository / "secrets")


def test_real_loopback_callback_with_mocked_identity_service(auth_factory, signing_key, capsys):
    import threading

    nonce = {}
    callback_responses = []
    failures = []
    workers = []
    manager = auth_factory(
        lambda request: httpx.Response(
            200, json=tokens(id_token=id_token(signing_key, nonce=nonce["value"]))
        )
    )

    def announce(text):
        if not text.startswith("https://auth.openai.com/"):
            return
        query = parse_qs(urlsplit(text).query)
        nonce["value"] = query["nonce"][0]
        destination = (
            query["redirect_uri"][0]
            + "?"
            + urlencode(
                {
                    "state": query["state"][0],
                    "code": "private-callback-code",
                    "client_id": "oaiapp_test",
                }
            )
        )

        def send_callback():
            try:
                with httpx.Client(trust_env=False, timeout=5) as local:
                    callback_responses.append(local.get(destination))
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=send_callback)
        workers.append(worker)
        worker.start()

    try:
        manager.login(port=0, open_browser=False, announce=announce, timeout=8)
    finally:
        for worker in workers:
            worker.join(8)
    assert failures == []
    assert callback_responses[0].status_code == 200
    assert callback_responses[0].headers["cache-control"] == "no-store"
    assert manager.status()["plan_permission"]
    captured = capsys.readouterr()
    assert "private-callback-code" not in captured.out + captured.err
