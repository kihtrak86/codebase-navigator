"""
Shared fixtures.

`temp_db` isolates each test to its own SQLite file so tests never see each
other's data or the real codenav.db a developer might have sitting around.
`make_repo` builds a real local git repo from a dict of {relpath: content}
so graph_builder tests exercise the actual `git clone` code path (cloning a
local path works exactly like cloning a remote URL) without hitting the
network -- fast and reliable in CI.
"""
import subprocess
import pytest
from fastapi.testclient import TestClient
from app import db as db_module
from app import auth as auth_module
from app.main import app


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_codenav.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_path))
    db_module.init_db()
    # login-rate-limit state is a module-level dict (see auth.py) so it
                                                                         
    monkeypatch.setattr(auth_module, "_login_attempts", {})
    yield
                                                  


@pytest.fixture
def test_user():
    """A signed-up user, for tests that need a user_id to scope indexed
    repos to (every repo is now owned by a user)."""
    return auth_module.create_user("tester@example.com", "correct horse battery staple")


@pytest.fixture
def authed_client():
    """A fresh TestClient per test (never a shared module-level instance --
    TestClient keeps a persistent cookie jar, and reusing one across tests
    would leak session cookies between them) already signed up and logged
    in as a throwaway user."""
    client = TestClient(app)
    res = client.post("/api/auth/signup", json={"email": "apitester@example.com", "password": "correct horse battery staple"})
    assert res.status_code == 200, res.text
    return client


@pytest.fixture
def make_repo(tmp_path):
    def _make(files: dict[str, str]) -> str:
        repo_dir = tmp_path / "fixture_repo"
        repo_dir.mkdir(exist_ok=True)
        for relpath, content in files.items():
            path = repo_dir / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
        subprocess.run(
            ["git", "-c", "user.email=test@test.com", "-c", "user.name=test", "commit", "-q", "-m", "init"],
            cwd=repo_dir, check=True,
        )
        return str(repo_dir)
    return _make
