# Changelog

## 0.1.0 — 2026-10-26

- Renumber the initial release to 0.1.0. Runtime source, provider behavior, public
  interfaces and dependency requirements are unchanged from the published 0.2.0.
- Update package metadata, the lockfile, release workflow and current installation
  instructions. Historical reports retain their original versions and test results.
- Keep the published 0.2.0 files and Git tag intact. Install 0.1.0 explicitly;
  unpinned version selection can still prefer 0.2.0.

## 0.2.0 — 2026-10-24

- Standalone Ollama and official ChatGPT OAuth clients, CLI and protected
  credential storage with serialized token renewal.
- General LangChain text/function-tool integration through `ChatRosaLLM`.
- Hardened native-history replay, tool-call validation, streaming consistency,
  structured errors and optional request records for research provenance.
- Python >=3.11 support, with Python 3.11/3.12 base and LangChain CI profiles.
  Development and Docker remain on Python 3.12 for the installed ROS 2 Jazzy.
- Apache-2.0 license. The original Git version reference is `v0.2.0`.

The dated reports in `docs/` distinguish automated tests, historical live
validation and provider limitations. This package does not execute agent tools
or implement a research algorithm.
