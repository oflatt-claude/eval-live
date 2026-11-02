"""Create the demo's two Git revisions from ordinary Python source files."""
from pathlib import Path
import shutil
import subprocess


def make_repository(path):
    if (path / ".git").exists():
        return
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"refusing to initialize nonempty demo repository: {path}")
    path.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent

    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    shutil.copyfile(source / "membership.py", path / "membership.py")
    for filename, message in (("baseline.py", "Baseline list membership"),
                              ("optimized.py", "Use a set for the set configuration")):
        shutil.copyfile(source / filename, path / "container.py")
        git("add", "membership.py", "container.py")
        git("-c", "user.name=eval-live demo", "-c", "user.email=demo@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", message)
