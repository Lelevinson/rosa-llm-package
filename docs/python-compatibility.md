**Python 3.11 support and verification**

For the later standalone Git/publication preparation, see
[pre-push review](publication-preparation.md). The results below retain their
original validation scope.

2026-10-24; rosa-llm 0.2.0. The base package and optional LangChain adapter now
require Python >=3.11. Verified versions are CPython 3.11.17 and 3.12.3 on
Linux/WSL x86_64. The checkout's `.python-version`, existing development `.venv`
and Docker image remain on Python 3.12 for the installed ROS 2 Jazzy binaries.

Changes are limited to metadata, locking, CI and documentation. Ruff targets
`py311`. Runtime module bytes, dependency declarations, public interfaces,
OpenAI OAuth/renewal/security behavior and Ollama/LangChain implementations are
unchanged. The baseline suite passed 254 tests before these changes.

The regenerated lock has 83 package entries. Its only dependency-version
addition is NumPy 2.4.6 for Python 3.11, satisfying the existing ROSA integration
test dependency. Python 3.12 retains NumPy 2.5.4. No runtime dependency was added.

[CI](../.github/workflows/tests.yml) now has four jobs: Python 3.11/3.12 crossed
with base/LangChain profiles. The LangChain jobs enable the extra and integration
test group. Job-level `UV_PYTHON` selects the matrix interpreter despite the
checkout's 3.12 pin. Tests, lint, formatting, lock validation and packaging run in
each job. This workflow was parsed and its four installation/test configurations
were exercised locally; no remote GitHub Actions run is claimed.

Verification results below show passed tests in each configuration:

| Python | Installation | Base | LangChain + integration dependencies |
| --- | --- | ---: | ---: |
| 3.11.17 | Locked checkout | 147 | 252 |
| 3.11.17 | Independent wheel environments | 147 | 252 |
| 3.12.3 | Locked checkout | 147 | 254 |
| 3.12.3 | Independent wheel environments | 147 | 254 |

Base suites have five optional skip reports. Python 3.11's adapter suite skips
the ROSA/Jazzy module: the installed ROS binaries target Python 3.12. Local
3.12 adapter runs source Jazzy and execute the two ROSA executor tests, including
sync/async tool execution. One existing upstream LangChain RunCollector/Pydantic
deprecation warning remains. Standard hosted CI has no ROS installation and
therefore skips those ROS-dependent cases on both interpreters.

Independent wheel environments imported the installed package in isolated
Python mode; base installations had no LangChain package. Each environment
passed dependency checks and its full applicable suite. Automated tests cover
OAuth/JWT validation and rotation, storage permissions/concurrency, provider
streams/errors, tool schemas/results, async behavior, request observation and
CLI behavior. No live inference or credential renewal was needed for this change.

Ruff lint/format and lock validation passed. Python 3.11 and 3.12 both built the
wheel and source archive. Final artifact checks verified `Requires-Python:
>=3.11`, Apache-2.0 metadata/license inclusion and unchanged packaged runtime
source. The output paths remain `dist/rosa_llm-0.2.0-py3-none-any.whl` and
`dist/rosa_llm-0.2.0.tar.gz`.

**ROSA Research remains a separate integration decision.** Changing this package
to >=3.11 does not by itself resolve uv's dependency resolution across ROSA
Research's declared >=3.9 range. uv checks the consumer's declared range, even
when the active interpreter is 3.12. A consumer still needs a compatible range
or a conditional dependency marker; a marker can omit this package on older
Python versions, but cannot make it run there. ROSA Research's default groups
and the local Jazzy binary installation impose additional 3.12 requirements.
No ROSA Research source, configuration, environment or Git metadata was changed;
no permanent integration, commit, push, publication or repository creation was
performed.

Python 3.9/3.10 are outside this package's supported metadata. Newer Python
versions and other platforms are not established by this matrix. Provider and
text/function-tool boundaries in [the hardening report](hardening-report.md)
still apply. The existing development environment remains Python 3.12.3.

ROSA Research preservation was checked against a complete baseline of 13,130
filesystem entries, including ignored configuration, its `.venv` and Git
metadata; no differences were found. Disposable test environments, the downloaded
3.11 interpreter and verification files were removed. Built distributions remain
in this package's ignored `dist/` directory.
