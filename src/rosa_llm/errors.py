"""Public errors contain safe descriptions, never raw HTTP bodies or credentials."""


class LLMError(Exception):
    code = "llm_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
        request_id: str | None = None,
        kind: str | None = None,
        param: str | None = None,
        attempt_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        self.request_id = request_id
        self.kind = kind
        self.param = param
        self.attempt_id = attempt_id

    def to_dict(self) -> dict:
        """Safe diagnostic fields; excludes arbitrary server messages and raw payloads."""
        result = {"code": self.code, "message": str(self)}
        for name in ("status_code", "provider_code", "request_id", "kind", "param", "attempt_id"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


class ConfigurationError(LLMError):
    code = "configuration_error"


class AuthenticationError(LLMError):
    code = "authentication_required"


class PermissionDenied(LLMError):
    code = "permission_denied"


class ProviderError(LLMError):
    code = "provider_error"


class UsageLimitError(ProviderError):
    code = "usage_limit"


class IncompleteResponse(ProviderError):
    code = "incomplete_response"


class UnsupportedFeature(LLMError):
    code = "unsupported_feature"
