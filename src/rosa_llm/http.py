"""Safe HTTP errors. Never relay arbitrary server bodies, URLs, or headers."""

import json
import math
import re

import httpx

from .errors import (
    AuthenticationError,
    LLMError,
    PermissionDenied,
    ProviderError,
    UnsupportedFeature,
    UsageLimitError,
)

LIMIT_CODES = {
    "subscription_sharing_usage_limit_exceeded",
}


def _raise_provider_error(code: str | None = None, status: int | None = None):
    if not isinstance(code, (str, type(None))):
        raise ProviderError("Provider returned an invalid error code.")
    if code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}:
        raise ProviderError(
            "Account usage or workspace availability cannot be checked; retry later."
        )
    if code == "subscription_sharing_user_not_eligible":
        raise PermissionDenied("This account/workspace is ineligible for ChatGPT plan inference.")
    if code == "subscription_sharing_unsupported_capability":
        raise UnsupportedFeature(
            "This input, model or tool is unsupported for ChatGPT plan inference."
        )
    if code in {"chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context"}:
        raise PermissionDenied(
            "The signed grant does not authorize inference; check the client and scopes."
        )
    if code == "subscription_sharing_invalid_user":
        raise AuthenticationError(
            "Subscriber identity could not be validated; check the selected account."
        )
    if code in LIMIT_CODES or status == 429:
        raise UsageLimitError("Account usage limit reached; review ChatGPT Settings Usage.")
    if status == 401:
        raise AuthenticationError(
            "Credentials were rejected; run rosa-llm login to authorize again."
        )
    if status == 403:
        raise PermissionDenied(
            "Account/workspace permission denied; check plan eligibility and grants."
        )
    if status == 404:
        raise ProviderError("Endpoint or model not found; check configuration and rosa-llm models.")
    raise ProviderError(
        "Provider rejected the request; check model support and service availability."
    )


def safe_identifier(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        return value
    return None


def raise_provider_error(code=None, status=None, *, request_id=None, param=None):
    try:
        _raise_provider_error(code, status)
    except LLMError as error:
        error.status_code = status
        error.provider_code = safe_identifier(code)
        error.request_id = safe_identifier(request_id)
        if isinstance(param, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,200}", param):
            error.param = param
        error.kind = "http_error" if status is not None else "provider_error"
        raise error from None


def transport_error(error: httpx.HTTPError) -> ProviderError:
    if isinstance(error, httpx.ConnectTimeout):
        kind = "connect_timeout"
    elif isinstance(error, httpx.ReadTimeout):
        kind = "read_timeout"
    elif isinstance(error, httpx.WriteTimeout):
        kind = "write_timeout"
    elif isinstance(error, httpx.PoolTimeout):
        kind = "pool_timeout"
    elif isinstance(error, httpx.ConnectError):
        kind = "connection_error"
    else:
        kind = "transport_error"
    return ProviderError(
        "Provider connection failed or timed out; partial output is not completion.",
        kind=kind,
    )


def strict_json_loads(payload: str) -> object:
    """Reject ambiguous objects and non-JSON numbers instead of altering tool arguments."""

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key.")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON number.")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("JSON number is outside the supported float range.")
        return number

    return json.loads(
        payload,
        object_pairs_hook=object_pairs,
        parse_constant=invalid_constant,
        parse_float=finite_float,
    )


def validate_json_body(body: dict):
    try:
        json.dumps(body, allow_nan=False)
    except (TypeError, ValueError):
        from .errors import ConfigurationError

        raise ConfigurationError(
            "Request messages, tools and options must contain JSON values."
        ) from None


def check_response(response: httpx.Response):
    if response.is_success:
        return
    code = None
    param = None
    try:
        data = response.json()
        error = data.get("error", {})
        if isinstance(error, dict):
            code = error.get("code")
            param = error.get("param")
    except (ValueError, AttributeError):
        pass
    raise_provider_error(
        code,
        response.status_code,
        request_id=response.headers.get("x-request-id"),
        param=param,
    )


def json_object(response: httpx.Response) -> dict:
    check_response(response)
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise ProviderError("Provider returned malformed JSON.")
