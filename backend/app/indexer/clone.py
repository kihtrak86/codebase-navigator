"""Clone a GitHub repository to a local working directory."""
import subprocess
import tempfile
import shutil
import os


class CloneError(Exception):
    pass


def clone_repo(url: str) -> tuple[str, str]:
    """
    Clones `url` into a fresh temp directory.
    Returns (local_path, commit_hash).
    Caller is responsible for cleaning up local_path (see cleanup_repo).
    """
    workdir = tempfile.mkdtemp(prefix="codenav_")
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, workdir],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise CloneError(f"git clone failed: {result.stderr.strip()}")

        rev = subprocess.run(
            ["git", "-C", workdir, "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        commit_hash = rev.stdout.strip() if rev.returncode == 0 else "unknown"
        return workdir, commit_hash
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def cleanup_repo(local_path: str):
    shutil.rmtree(local_path, ignore_errors=True)


def iter_source_files(local_path: str, extensions=(".py",)):
    """Yields (absolute_path, relative_path) for source files, skipping .git and common noise dirs."""
    skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".mypy_cache"}
    for root, dirs, files in os.walk(local_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f.endswith(extensions):
                abspath = os.path.join(root, f)
                relpath = os.path.relpath(abspath, local_path)
                yield abspath, relpath
