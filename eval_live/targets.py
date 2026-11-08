"""Resolve immutable Git targets and manage library-owned detached worktrees."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
from typing import Iterator


def git(repository: Path, *arguments: str) -> str:
    """Run Git with an argument vector and return UTF-8 output."""
    result = subprocess.run(["git", "-C", str(repository), *arguments],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(f"Git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


@dataclass(frozen=True)
class Target:
    """A ref resolved to a repository and full commit before collection."""

    repository: Path
    identity: str
    ref: str
    commit: str

    @classmethod
    def resolve(cls, repository: str | Path, ref: str) -> Target:
        """Resolve a ref without modifying or checking out the user's tree."""
        root = Path(git(Path(repository).expanduser().resolve(), "rev-parse", "--show-toplevel"))
        sha = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
        common = git(root, "rev-parse", "--git-common-dir")
        identity = str((root / common).resolve())
        return cls(root, identity, ref, sha)

    @contextmanager
    def checkout(self) -> Iterator[Path]:
        """Yield a working directory and remove only library-owned worktrees."""
        with tempfile.TemporaryDirectory(prefix="eval-live-") as temporary:
            directory = Path(temporary) / "target"
            try:
                git(self.repository, "worktree", "add", "--quiet", "--detach", str(directory), self.commit)
                yield directory
            finally:
                if (directory / ".git").exists():
                    git(self.repository, "worktree", "remove", "--force", str(directory))


def script_revision(script: Path | None) -> dict:
    """Describe the evaluation script separately from benchmark target revisions."""
    result = {"script": str(script) if script else None, "script_commit": None, "script_dirty": None}
    if script and script.is_file():
        try:
            result["script_commit"] = git(script.parent, "rev-parse", "HEAD")
            result["script_dirty"] = bool(git(script.parent, "status", "--porcelain"))
        except ValueError:
            pass
    return result
