"""Owner-only atomic files and Linux process locks; no database or third-party lock server."""

import fcntl
import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .errors import AuthenticationError, ConfigurationError


def is_git_root(path: Path) -> bool:
    # An empty .git directory is not a repository (some sandboxes mount placeholders).
    marker = path / ".git"
    return marker.is_file() or (marker / "HEAD").is_file()


class CredentialStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).expanduser().absolute()
        # Never store secrets in the current project or this package's source project.
        roots = [
            p
            for p in (Path.cwd(), *Path.cwd().parents)
            if (p / "pyproject.toml").exists() or is_git_root(p)
        ]
        source_root = Path(__file__).resolve().parents[2]
        if (source_root / "pyproject.toml").exists():
            roots.append(source_root)
        if any(self.directory.resolve().is_relative_to(root.resolve()) for root in roots):
            raise ConfigurationError("Credential directory must be outside the project.")
        if any(
            is_git_root(p) for p in (self.directory.resolve(), *self.directory.resolve().parents)
        ):
            raise ConfigurationError("Credential directory must be outside every Git repository.")

    def _prepare(self):
        if self.directory.is_symlink():
            raise AuthenticationError("Credential directory must not be a symlink.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = self.directory.stat()
        if info.st_uid != os.getuid():
            raise AuthenticationError("Credential directory belongs to another user.")
        self.directory.chmod(0o700)

    @contextmanager
    def lock(self):
        self._prepare()
        fd = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
                raise AuthenticationError("Unsafe credential lock file.")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self, name: str) -> dict:
        try:
            fd = os.open(self.directory / name, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {}
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
                raise AuthenticationError("Credential files must be owner-only (chmod 600).")
            with os.fdopen(fd, encoding="utf-8") as stream:
                fd = -1
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError):
            raise AuthenticationError(
                "Credential file is invalid; restore it or log in again."
            ) from None
        finally:
            if fd >= 0:
                os.close(fd)

    def write(self, name: str, data: dict):
        """Caller holds lock. fsync then replace so readers never see half a token set."""
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix=".pending-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / name)
            directory_fd = os.open(self.directory, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def host_id(self) -> str:
        with self.lock():
            host = self.read("host.json")
            if not host:
                host = {"ext_agent_host_id": f"urn:uuid:{uuid.uuid4()}"}
                self.write("host.json", host)
            if not isinstance(host.get("ext_agent_host_id"), str) or not host["ext_agent_host_id"]:
                raise AuthenticationError("Saved host identity is invalid; restore host.json.")
            return host["ext_agent_host_id"]
