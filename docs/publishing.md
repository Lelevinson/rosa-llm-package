# Publishing rosa-llm to PyPI

The release workflow is `.github/workflows/publish.yml` (**Publish to PyPI**).
It runs manually from `main`. Its default is a dry run: checks and builds execute,
but no package is uploaded. Runtime code and ROSA Research are outside this setup.

## One-time account setup

The `rosa-llm` project is now published with its GitHub Trusted Publisher configured.
The following records the initial setup; do not add another pending publisher for
the existing project. Manage its publisher through the project's PyPI settings.

For initial publication, create and verify your PyPI account and enable two-factor
authentication. Then, while signed in, open
[account publishing](https://pypi.org/manage/account/publishing/)
and add a **pending GitHub Trusted Publisher** with these values:

| PyPI field | Value |
| --- | --- |
| PyPI project name | `rosa-llm` |
| GitHub owner | `Lelevinson` |
| Repository | `rosa-llm-package` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

The workflow filename is the filename only, not its full path. The GitHub `pypi`
environment allows deployments from `main`. No PyPI password or long-lived API
token belongs in the repository or GitHub secrets. GitHub obtains a short-lived
publishing credential through OIDC when the publish job runs.

A pending publisher does not reserve a name. A 404 from PyPI's project API means
no public project currently exists; it is not a guarantee the name can be claimed.
See [PyPI's setup instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Release procedure

1. Use an existing version tag whose package name/version match `pyproject.toml`.
   The intended initial-release number is now `v0.1.0`. The original `v0.2.0`
   upload and tag remain intact; see the release history below. Future releases
   need a new package version and corresponding tag.
2. In GitHub **Actions → Publish to PyPI → Run workflow**, select branch `main`,
   set `release_tag` to the intended release tag, and leave `publish` **false**.
3. Inspect the completed run: Python 3.11/3.12 base and LangChain checks, wheel and
   source distribution, metadata/README checks, and the source/distribution hashes.
   Download the `release-distributions` artifact if needed.
4. Confirm the Trusted Publisher is configured. Run the same workflow/tag with
   `publish` **true** to upload after all checks pass. The publish job uses only
   the distributions built in that run; it does not rebuild under publishing credentials.
5. Verify the project/version/files on PyPI and install the published wheel in a
   fresh environment. Check both the base client and the `[langchain]` extra.

The tag is resolved to one commit before testing/building, so all jobs check the
same source. Existing push/PR checks retain their normal source revision.
Only the upload job receives `id-token: write`; no model credentials or live
inference are needed for these automated checks.

Building a release uses the README stored in its tag. Later README edits on `main`
will appear in a future release; they do not alter an existing release snapshot.
PyPI files cannot be overwritten: a changed release needs a new version and tag.
The workflow deliberately fails on an already-uploaded file rather than hiding it.

## Release numbering

The original published release was 0.2.0, from tag `v0.2.0` at commit
`0b15070f57b9b34790f7fb4c1330a0b19bf69f98`. The owner subsequently selected 0.1.0
as the initial-release number. This is a separate release with identical runtime
source; it does not rename, delete or overwrite the original PyPI upload or Git tag.
Historical reports keep their original version labels and evidence.

Install 0.1.0 explicitly with `uv add "rosa-llm[langchain]==0.1.0"`.
An unpinned install can still select 0.2.0 because it sorts higher. Subsequent
releases intended for automatic upgrades should exceed 0.2.0.

Git consumers can continue using the existing fixed commit and lockfile.
Publishing does not modify their dependencies or configure their model credentials.
