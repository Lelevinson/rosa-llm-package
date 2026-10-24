# Protocol references

Reviewed on 2026-10-22. Account eligibility and preview behavior may change.
These are implementation references, not evidence that a real account was authorized.

- [Overview](https://developers.openai.com/siwc/token-sharing-open-source):
  separates OAuth registration from the persistent identity of a runtime host.
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in):
  initial dynamic registration, issued client IDs, PKCE, state, nonce, exact
  loopback callback matching, verified ID tokens and protected credential records.
  Implemented by `PendingLogin`, `parse_callback`, `OAuthManager.finish_login`,
  and `CredentialStore`. Authorization URLs printed for manual browser use omit
  retained ID-token hints.
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions):
  profile separation, returning authorization, serialized rotating refresh, and
  revocation discovered via OpenID configuration. Implemented by `OAuthManager`.
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference):
  token fields and expiries. Expiry is calculated from the received `expires_in`;
  the implementation does not assume a fixed access-token lifetime.
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference):
  account catalog selection and public streamed Responses requests. Implemented
  by `OpenAIProvider.models`, `model`, and `stream`.
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations):
  stateless HTTP history, namespace-wrapped tools and restricted parameters.
  This adapter deliberately exposes a smaller text/tool surface and rejects
  other options, rather than forwarding arbitrary API fields.
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery):
  identity-only grants, renewed consent, terminal refresh errors and usage failures.
  The implementation reports safe errors without logging raw server bodies.
- [Website ID-token validation](https://developers.openai.com/siwc/website):
  production discovery, RSA/JWKS signature and identity claim checks. This package
  remains a public client and does not use the website flow's client secrets.
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling):
  namespace schema and explicit function results. The caller validates and runs
  local functions; provider output is retained for the next request.
- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning):
  preserve output items, including encrypted reasoning, for stateless continuation.
- [Ollama chat](https://docs.ollama.com/api/chat) and
  [model listing](https://docs.ollama.com/api/tags): native chat streaming,
  function tools, token/duration metadata, and installed models.

Only the documented public OpenAI endpoints are used. This package neither
reads nor modifies Codex authentication or its underlying model configuration.
