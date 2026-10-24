"""Official public-client ChatGPT plan OAuth. See docs/protocol.md for sources."""

import base64
import hashlib
import secrets
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from .errors import AuthenticationError, LLMError, PermissionDenied
from .http import json_object, safe_identifier
from .storage import CredentialStore

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
DISCOVERY = ISSUER + "/.well-known/openid-configuration"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}


@dataclass(repr=False)
class PendingLogin:
    client_id: str
    redirect_uri: str
    host_id: str
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64))
    created_at: float = field(default_factory=time.time)
    consumed: bool = False

    @property
    def challenge(self) -> str:
        return (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

    def url(
        self, previous: dict | None = None, *, hints: bool = False, consent: bool = False
    ) -> str:
        params = {
            "client_id": self.client_id,
            "ext_agent_host_id": self.host_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge_method": "S256",
            "code_challenge": self.challenge,
        }
        if self.client_id == "dynamic_agent_client":
            params["agent_name_hint"] = "rosa-llm"
        elif previous and hints:
            # Never print URLs containing the retained ID token.
            if previous.get("id_token"):
                params["id_token_hint"] = previous["id_token"]
            if previous.get("email"):
                params["login_hint"] = previous["email"]
        if consent:
            params["prompt"] = "consent"
        return AUTHORIZE + "?" + urlencode(params)


def parse_callback(query: str, pending: PendingLogin) -> tuple[str, str]:
    if pending.consumed or time.time() - pending.created_at > 300:
        raise AuthenticationError("Login attempt expired or already consumed; start login again.")
    pending.consumed = True
    params = parse_qs(query, keep_blank_values=True)
    if any(len(values) != 1 for values in params.values()):
        raise AuthenticationError("OAuth callback contains duplicate parameters.")
    state = params.get("state", [""])[0]
    if not secrets.compare_digest(state.encode(), pending.state.encode()):
        raise AuthenticationError("OAuth state mismatch; start login again.")
    if "error" in params:
        raise PermissionDenied("Authorization was denied; no credentials were changed.")
    client_id = params.get("client_id", [pending.client_id])[0]
    if pending.client_id == "dynamic_agent_client":
        if not client_id or client_id == "dynamic_agent_client":
            raise AuthenticationError("Registration callback did not supply an issued client ID.")
    elif client_id != pending.client_id:
        raise AuthenticationError("Callback client ID does not match the selected registration.")
    code = params.get("code", [""])[0]
    if not code:
        raise AuthenticationError("OAuth callback did not contain a code.")
    return code, client_id


class OAuthManager:
    def __init__(self, store: CredentialStore, http: httpx.Client, profile: str = "default"):
        self.store, self.http, self.profile = store, http, profile

    def _record(self) -> dict:
        record = self.store.read("credentials.json").get(self.profile, {})
        if not isinstance(record, dict):
            raise AuthenticationError(
                "Invalid saved profile; restore credentials or authorize again."
            )
        if record:
            if not all(
                isinstance(record.get(key), str) and record[key] for key in ("client_id", "subject")
            ):
                raise AuthenticationError("Saved profile is missing its registration identity.")
            if (
                not isinstance(record.get("scopes", []), list)
                or not all(isinstance(scope, str) for scope in record.get("scopes", []))
                or not isinstance(record.get("expires_at", 0), (float, int))
            ):
                raise AuthenticationError("Saved profile has invalid scopes or expiry information.")
        return record

    def _invalidate(self, record: dict):
        record = {
            k: v
            for k, v in record.items()
            if k not in {"access_token", "refresh_token", "id_token", "pending_id_token"}
        }
        record["needs_login"] = True
        self._save(record)

    def _validate_pending_identity(self, record: dict):
        token = record.get("pending_id_token")
        if not token:
            return
        try:
            claims = self.validate_identity(token, record["client_id"])
            if claims["sub"] != record["subject"]:
                raise AuthenticationError("Refreshed token identity changed; authorize again.")
        except AuthenticationError:
            self._invalidate(record)
            raise
        # Network/JWKS outages leave the rotated tokens pending and do not permit inference.
        record["id_token"] = record.pop("pending_id_token")
        self._save(record)

    def _save(self, record: dict):
        records = self.store.read("credentials.json")
        records[self.profile] = record
        self.store.write("credentials.json", records)

    def status(self) -> dict:
        with self.store.lock():
            record = self._record()
            pending_validation = bool(record.get("pending_id_token"))
            authenticated = bool(record.get("access_token")) and not pending_validation
            return {
                "profile": self.profile,
                "authenticated": authenticated,
                "identity_authenticated": bool(record.get("id_token")),
                "identity_validation_pending": pending_validation,
                "plan_permission": authenticated and PLAN_SCOPES.issubset(record.get("scopes", [])),
                "expired": record.get("expires_at", 0) <= time.time(),
                "renewable": bool(record.get("refresh_token")),
                "remote_permission_checked": False,
            }

    def _discovery(self) -> dict:
        doc = json_object(self.http.get(DISCOVERY, follow_redirects=False))
        if doc.get("issuer") != ISSUER:
            raise AuthenticationError("OIDC discovery issuer mismatch.")
        # Discovery cannot redirect secrets to an arbitrary origin.
        for name in ("jwks_uri", "revocation_endpoint"):
            endpoint = doc.get(name)
            if endpoint and (
                urlsplit(endpoint).scheme != "https"
                or urlsplit(endpoint).netloc != "auth.openai.com"
            ):
                raise AuthenticationError("OIDC discovery returned an untrusted endpoint.")
        return doc

    def validate_identity(self, token: str, client_id: str, nonce: str | None = None) -> dict:
        try:
            doc = self._discovery()
            jwks = json_object(self.http.get(doc["jwks_uri"], follow_redirects=False))
            header = jwt.get_unverified_header(token)
            # Algorithms are chosen by the application, never trusted from the JWT alone.
            if header.get("alg") != "RS256":
                raise ValueError
            keys = [
                key
                for key in jwks["keys"]
                if key.get("kid") == header.get("kid")
                and key.get("kty") == "RSA"
                and key.get("use", "sig") == "sig"
            ]
            if len(keys) != 1:
                raise ValueError
            key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256")
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=ISSUER,
                audience=client_id,
                leeway=5,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                raise ValueError
            if nonce is not None and (
                not isinstance(claims.get("nonce"), str)
                or not secrets.compare_digest(claims["nonce"], nonce)
            ):
                raise ValueError
            if isinstance(claims["aud"], list) and len(claims["aud"]) > 1:
                if claims.get("azp") != client_id:
                    raise ValueError
            return claims
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            raise AuthenticationError(
                "ID token signature, identity, expiry, or nonce is invalid."
            ) from None

    def _tokens(self, form: dict) -> dict:
        response = self.http.post(TOKEN, data=form, follow_redirects=False)
        if not response.is_success:
            try:
                error = response.json().get("error")
            except (ValueError, AttributeError):
                error = None
            if isinstance(error, dict):
                error = error.get("code")
            if isinstance(error, str) and error in {
                "invalid_grant",
                "invalid_client",
                "invalid_refresh_token",
                "token_expired",
                "refresh_token_expired",
                "refresh_token_invalidated",
                "refresh_token_reused",
            }:
                raise AuthenticationError(
                    "Session or authorization code is invalid; run rosa-llm login.",
                    status_code=response.status_code,
                    provider_code=safe_identifier(error),
                    request_id=safe_identifier(response.headers.get("x-request-id")),
                    kind="http_error",
                )
        return json_object(response)

    @staticmethod
    def _token_record(tokens: dict, old: dict | None = None) -> dict:
        old = old or {}
        if (
            not isinstance(tokens.get("access_token"), str)
            or not tokens["access_token"]
            or str(tokens.get("token_type", "")).lower() != "bearer"
        ):
            raise AuthenticationError("Token endpoint returned an invalid access token response.")
        try:
            lifetime = float(tokens["expires_in"])
            if not 0 < lifetime <= 86400:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise AuthenticationError(
                "Token endpoint returned invalid expiry information."
            ) from None
        if not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"]:
            raise AuthenticationError(
                "No rotating refresh token was returned; authorize offline access."
            )
        scope = tokens.get("scope")
        scopes = scope.split() if isinstance(scope, str) else old.get("scopes", [])
        if not PLAN_SCOPES.issubset(scopes):
            raise PermissionDenied(
                "Identity login succeeded but ChatGPT plan inference was not granted."
            )
        return {
            **old,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "token_type": "Bearer",
            "expires_at": time.time() + lifetime,
            "scopes": scopes,
            "saved_at": time.time(),
            "needs_login": False,
        }

    def finish_login(self, pending: PendingLogin, query: str, previous: dict | None = None):
        code, client_id = parse_callback(query, pending)
        tokens = self._tokens(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": pending.verifier,
                "redirect_uri": pending.redirect_uri,
                "resource": RESOURCE,
            }
        )
        claims = self.validate_identity(tokens.get("id_token", ""), client_id, pending.nonce)
        if previous and (
            claims["sub"] != previous["subject"] or client_id != previous["client_id"]
        ):
            raise AuthenticationError(
                "Signed-in identity differs from the selected profile; use a new profile."
            )
        identity = {
            "subject": claims["sub"],
            "email": claims.get("email"),
            "issuer": ISSUER,
            "client_id": client_id,
            "id_token": tokens["id_token"],
            "ext_agent_host_id": pending.host_id,
        }
        scope = tokens.get("scope")
        if not isinstance(scope, str) or not PLAN_SCOPES.issubset(scope.split()):
            # Retain a new identity for reauthorization; preserve a valid old session.
            with self.store.lock():
                if self._record() != (previous or {}):
                    raise AuthenticationError("Profile changed during login; start login again.")
                if not previous or not previous.get("access_token"):
                    self._save(
                        {
                            **identity,
                            "scopes": scope.split() if isinstance(scope, str) else [],
                            "expires_at": 0,
                        }
                    )
            raise PermissionDenied(
                "Identity login succeeded but plan inference was not granted; authorize plan usage."
            )
        record = {**self._token_record(tokens), **identity}
        with self.store.lock():
            if self._record() != (previous or {}):
                raise AuthenticationError("Profile changed during login; start login again.")
            self._save(record)

    def access_token(self) -> str:
        # Re-read AFTER acquiring lock: a second process sees the first process's rotated tokens.
        with self.store.lock():
            record = self._record()
            if record.get("id_token") and not PLAN_SCOPES.issubset(record.get("scopes", [])):
                raise PermissionDenied(
                    "Identity is signed in, but ChatGPT plan inference is disabled."
                )
            if not record.get("access_token") or record.get("needs_login"):
                raise AuthenticationError("Human authentication required: run rosa-llm login.")
            if not PLAN_SCOPES.issubset(record.get("scopes", [])):
                raise PermissionDenied("This login has no ChatGPT plan inference permission.")
            self._validate_pending_identity(record)
            if record.get("expires_at", 0) > time.time() + 30:
                return record["access_token"]
            if not record.get("refresh_token"):
                raise AuthenticationError("Session cannot be renewed; run rosa-llm login.")
            try:
                tokens = self._tokens(
                    {
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    }
                )
            except AuthenticationError:
                self._invalidate(record)
                raise
            try:
                updated = self._token_record(tokens, record)
            except (AuthenticationError, PermissionDenied):
                # The old refresh token may already be consumed; never keep retrying it.
                self._invalidate(record)
                raise
            if tokens.get("id_token"):
                updated["pending_id_token"] = tokens["id_token"]
            # Persist rotation before fetching validation keys: a network failure must not
            # lose the replacement refresh token or retry its already-consumed predecessor.
            self._save(updated)
            self._validate_pending_identity(updated)
            return updated["access_token"]

    def logout(self) -> dict:
        confirmed = True
        with self.store.lock():
            record = self._record()
            if record.get("refresh_token"):
                confirmed = False
                try:
                    endpoint = self._discovery()["revocation_endpoint"]
                    for attempt in range(3):
                        try:
                            response = self.http.post(
                                endpoint,
                                follow_redirects=False,
                                data={
                                    "token": record["refresh_token"],
                                    "token_type_hint": "refresh_token",
                                    "client_id": record["client_id"],
                                },
                            )
                            if response.status_code == 200:
                                confirmed = True
                                break
                            if response.status_code < 500:
                                break
                        except httpx.TransportError:
                            pass
                        if attempt < 2:
                            time.sleep(0.2 * 2**attempt)
                except (httpx.HTTPError, KeyError, LLMError):
                    pass
            for key in ("access_token", "refresh_token", "id_token", "pending_id_token"):
                record.pop(key, None)
            if record:
                self._save(record)
        return {
            "profile": self.profile,
            "authenticated": False,
            "remote_revocation_confirmed": confirmed,
        }

    def login(
        self,
        *,
        port: int = 1455,
        open_browser: bool = True,
        consent: bool = False,
        announce: Callable[[str], None] = print,
        timeout: float = 180,
    ):
        """Loopback only. Docker uses host networking, never a public callback bind."""
        host_id = self.store.host_id()
        with self.store.lock():
            previous = self._record()
        manager = self
        result: dict = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # BaseHTTPRequestHandler would otherwise log callback secrets.

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path != "/auth/callback":
                    self.send_error(404)
                    return
                try:
                    manager.finish_login(pending, parsed.query, previous)
                    result["ok"] = True
                    body = b"Sign-in complete. You can close this tab."
                except Exception as error:
                    result["error"] = error
                    body = b"Sign-in failed. Check the terminal for a safe error description."
                self.send_response(200 if result.get("ok") else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class LocalServer(HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(3)
                return connection, address

            def handle_error(self, request, client_address):
                pass  # No tracebacks with request/token details.

        with LocalServer(("127.0.0.1", port), CallbackHandler) as server:
            server.timeout = 0.5
            pending = PendingLogin(
                previous.get("client_id", "dynamic_agent_client"),
                f"http://127.0.0.1:{server.server_port}/auth/callback",
                host_id,
            )
            opened = False
            if open_browser:
                try:
                    opened = webbrowser.open(pending.url(previous, hints=True, consent=consent))
                except webbrowser.Error:
                    pass
            if not opened:
                announce("Open this authorization URL in your browser (keep it private):")
                announce(pending.url(consent=consent))  # No saved identity hints in output.
            announce("Waiting for local callback; complete authorization in the browser.")
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                server.handle_request()
            if "error" in result:
                raise result["error"]
            if not result.get("ok"):
                raise AuthenticationError(
                    "Login timed out; check loopback forwarding and try again."
                )
