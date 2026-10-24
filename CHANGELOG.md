# Changelog

## 0.2.0 — 2026-10-24

- Standalone Ollama and official ChatGPT OAuth clients, CLI and protected
  credential storage with serialized token renewal.
- General LangChain text/function-tool integration through `ChatRosaLLM`.
- Hardened native-history replay, tool-call validation, streaming consistency,
  structured errors and optional request records for research provenance.
- Python >=3.11 support, with Python 3.11/3.12 base and LangChain CI profiles.
  Development and Docker remain on Python 3.12 for the installed ROS 2 Jazzy.
- Apache-2.0 license. The initial Git version reference is planned as `v0.2.0`.

The dated reports in `docs/` distinguish automated tests, historical live
validation and provider limitations. This package does not execute agent tools
or implement a research algorithm.
