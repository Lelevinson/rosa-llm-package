"""Local package identity. No network, credential reads, or imports of optional frameworks."""

import hashlib
import platform
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def package_info() -> dict:
    package_dir = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    sources = sorted(package_dir.glob("*.py"))
    for source in sources:
        name = "rosa_llm/" + source.name
        digest.update(name.encode() + b"\0" + source.read_bytes() + b"\0")
    dependencies = {}
    for name in (
        "rosa-llm",
        "httpx",
        "PyJWT",
        "python-dotenv",
        "langchain-core",
        "langchain",
        "pydantic",
    ):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            continue
    result = {
        "version": dependencies.get("rosa-llm"),
        "source_sha256": digest.hexdigest() if sources else None,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": dependencies,
        "git_revision": None,
        "git_dirty": None,
    }
    # Inspect only this source checkout, never an unrelated enclosing repository.
    root = package_dir.parent.parent
    if (root / "pyproject.toml").is_file() and (root / ".git").exists():
        try:
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            status = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            result["git_revision"] = revision.stdout.strip()
            result["git_dirty"] = bool(status.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    return result
