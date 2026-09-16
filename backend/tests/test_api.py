"""
Tests the HTTP layer (routes.py + auth_routes.py) end-to-end via FastAPI's
TestClient, against local fixture repos -- covers the same contract the
frontend relies on: response shapes, status codes, auth requirements, and
per-user data isolation.
"""
from fastapi.testclient import TestClient
from app.main import app
from app.api import routes
from app.db import get_conn


def test_health():
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_repo_endpoints_require_auth():
    client = TestClient(app)  # no signup/login
    assert client.get("/api/repos").status_code == 401
    assert client.post("/api/repos/index", json={"url": "whatever"}).status_code == 401


def test_signup_then_login_flow():
    client = TestClient(app)
    res = client.post("/api/auth/signup", json={"email": "new@example.com", "password": "correct horse battery staple"})
    assert res.status_code == 200
    assert res.json()["email"] == "new@example.com"

    # signed in immediately after signup
    assert client.get("/api/auth/me").json()["email"] == "new@example.com"

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() is None

    res = client.post("/api/auth/login", json={"email": "new@example.com", "password": "correct horse battery staple"})
    assert res.status_code == 200
    assert client.get("/api/auth/me").json()["email"] == "new@example.com"


def test_signup_rejects_duplicate_email():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "dupe@example.com", "password": "correct horse battery staple"})
    res = client.post("/api/auth/signup", json={"email": "dupe@example.com", "password": "another password here"})
    assert res.status_code == 400


def test_signup_rejects_short_password():
    client = TestClient(app)
    res = client.post("/api/auth/signup", json={"email": "a@example.com", "password": "short"})
    assert res.status_code == 422


def test_login_rejects_wrong_password():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "b@example.com", "password": "correct horse battery staple"})
    res = client.post("/api/auth/login", json={"email": "b@example.com", "password": "wrong password entirely"})
    assert res.status_code == 401


def test_login_rate_limited_after_repeated_failures():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "locked@example.com", "password": "correct horse battery staple"})
    client.post("/api/auth/logout")
    for _ in range(5):
        res = client.post("/api/auth/login", json={"email": "locked@example.com", "password": "wrong password"})
        assert res.status_code == 401
    # 6th attempt, even with the correct password, is rate-limited
    res = client.post("/api/auth/login", json={"email": "locked@example.com", "password": "correct horse battery staple"})
    assert res.status_code == 429


def test_successful_login_clears_rate_limit():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "recover@example.com", "password": "correct horse battery staple"})
    client.post("/api/auth/logout")
    for _ in range(3):
        client.post("/api/auth/login", json={"email": "recover@example.com", "password": "wrong password"})
    res = client.post("/api/auth/login", json={"email": "recover@example.com", "password": "correct horse battery staple"})
    assert res.status_code == 200


def test_forgot_password_does_not_reveal_account_existence():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "known@example.com", "password": "correct horse battery staple"})
    res_known = client.post("/api/auth/forgot-password", json={"email": "known@example.com"})
    res_unknown = client.post("/api/auth/forgot-password", json={"email": "nosuchaccount@example.com"})
    assert res_known.status_code == 200 and res_unknown.status_code == 200
    assert res_known.json()["message"] == res_unknown.json()["message"]
    assert res_known.json()["dev_reset_token"] is not None
    assert res_unknown.json()["dev_reset_token"] is None


def test_reset_password_with_valid_token_then_login():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "reset@example.com", "password": "old password here"})
    client.post("/api/auth/logout")
    token = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"}).json()["dev_reset_token"]

    res = client.post("/api/auth/reset-password", json={"token": token, "password": "brand new password"})
    assert res.status_code == 200

    # old password no longer works, new one does
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": "old password here"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "reset@example.com", "password": "brand new password"}).status_code == 200


def test_reset_password_token_cannot_be_reused():
    client = TestClient(app)
    client.post("/api/auth/signup", json={"email": "onceonly@example.com", "password": "old password here"})
    token = client.post("/api/auth/forgot-password", json={"email": "onceonly@example.com"}).json()["dev_reset_token"]
    assert client.post("/api/auth/reset-password", json={"token": token, "password": "first new password"}).status_code == 200
    res = client.post("/api/auth/reset-password", json={"token": token, "password": "second new password"})
    assert res.status_code == 400


def test_reset_password_rejects_invalid_token():
    client = TestClient(app)
    res = client.post("/api/auth/reset-password", json={"token": "not-a-real-token", "password": "whatever password"})
    assert res.status_code == 400


def test_change_password_requires_auth():
    client = TestClient(app)
    res = client.post("/api/auth/change-password", json={"current_password": "a", "new_password": "brand new password"})
    assert res.status_code == 401


def test_change_password_requires_correct_current_password(authed_client):
    res = authed_client.post("/api/auth/change-password", json={"current_password": "wrong one", "new_password": "brand new password"})
    assert res.status_code == 400


def test_change_password_success_then_relogin(authed_client):
    res = authed_client.post("/api/auth/change-password", json={
        "current_password": "correct horse battery staple",
        "new_password": "a brand new password",
    })
    assert res.status_code == 200
    authed_client.post("/api/auth/logout")
    res = authed_client.post("/api/auth/login", json={"email": "apitester@example.com", "password": "a brand new password"})
    assert res.status_code == 200


def test_index_and_fetch_repo(authed_client, make_repo):
    repo_path = make_repo({"a.py": "def foo():\n    return bar()\n\ndef bar():\n    return 1\n"})

    res = authed_client.post("/api/repos/index", json={"url": repo_path})
    assert res.status_code == 200
    body = res.json()
    assert body["file_count"] == 1
    assert body["symbol_count"] == 2
    repo_id = body["repository_id"]

    res = authed_client.get("/api/repos")
    assert res.status_code == 200
    assert any(r["id"] == repo_id for r in res.json())

    res = authed_client.get(f"/api/repos/{repo_id}/tree")
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_index_invalid_repo_returns_400(authed_client):
    res = authed_client.post("/api/repos/index", json={"url": "/nonexistent/path/that/is/not/a/repo"})
    assert res.status_code == 400


def test_symbol_detail_impact_and_graph(authed_client, make_repo):
    repo_path = make_repo({
        "app.py": "def foo():\n    return bar()\n\ndef bar():\n    return 1\n",
    })
    repo_id = authed_client.post("/api/repos/index", json={"url": repo_path}).json()["repository_id"]

    search_res = authed_client.get(f"/api/repos/{repo_id}/search", params={"q": "bar"})
    assert search_res.status_code == 200
    bar_id = search_res.json()[0]["id"]

    detail_res = authed_client.get(f"/api/symbols/{bar_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["name"] == "bar"
    assert {c["qualified_name"] for c in detail["callers"]} == {"foo"}

    impact_res = authed_client.get(f"/api/symbols/{bar_id}/impact")
    assert impact_res.status_code == 200
    assert {s["qualified_name"] for s in impact_res.json()["affected_symbols"]} == {"foo"}

    graph_res = authed_client.get(f"/api/symbols/{bar_id}/graph")
    assert graph_res.status_code == 200
    graph = graph_res.json()
    node_labels = {n["label"] for n in graph["nodes"]}
    assert node_labels == {"foo", "bar"}
    assert len(graph["edges"]) == 1


def test_symbol_not_found_returns_404(authed_client):
    res = authed_client.get("/api/symbols/999999")
    assert res.status_code == 404


def test_cannot_access_another_users_repo_or_symbols(make_repo):
    """The core of per-user data isolation: user A's repo/symbol IDs must
    404 for user B, not leak data just because IDs are guessable integers."""
    repo_path = make_repo({"a.py": "def foo():\n    return 1\n"})

    client_a = TestClient(app)
    client_a.post("/api/auth/signup", json={"email": "a@example.com", "password": "correct horse battery staple"})
    repo_id = client_a.post("/api/repos/index", json={"url": repo_path}).json()["repository_id"]
    symbol_id = client_a.get(f"/api/repos/{repo_id}/search", params={"q": "foo"}).json()[0]["id"]

    client_b = TestClient(app)
    client_b.post("/api/auth/signup", json={"email": "b@example.com", "password": "correct horse battery staple"})

    assert client_b.get(f"/api/repos/{repo_id}/tree").status_code == 404
    assert client_b.get(f"/api/symbols/{symbol_id}").status_code == 404
    assert client_b.delete(f"/api/repos/{repo_id}").status_code == 404
    # and user A's own list must not be affected by B's attempts
    assert any(r["id"] == repo_id for r in client_a.get("/api/repos").json())


def test_delete_repo(authed_client, make_repo):
    repo_path = make_repo({"a.py": "def foo():\n    return 1\n"})
    repo_id = authed_client.post("/api/repos/index", json={"url": repo_path}).json()["repository_id"]

    res = authed_client.delete(f"/api/repos/{repo_id}")
    assert res.status_code == 200

    res = authed_client.get("/api/repos")
    assert all(r["id"] != repo_id for r in res.json())

    res = authed_client.delete(f"/api/repos/{repo_id}")
    assert res.status_code == 404  # already gone


def test_reindexing_replaces_not_duplicates_via_api(authed_client, make_repo):
    repo_path = make_repo({"a.py": "def foo():\n    return 1\n"})
    authed_client.post("/api/repos/index", json={"url": repo_path})
    authed_client.post("/api/repos/index", json={"url": repo_path})

    res = authed_client.get("/api/repos")
    matching = [r for r in res.json() if r["url"] == repo_path]
    assert len(matching) == 1


# ---------------------------------------------------------------------------
# Nested file tree (round nine)
# ---------------------------------------------------------------------------

def test_build_file_tree_nests_folders_and_rolls_up_counts():
    """Unit test on the pure function, so tree shape is verified without
    standing up a repo: folders nest, counts roll up through every level."""
    rows = [
        {"id": 1, "path": "src/auth/login.py", "language": "python", "symbol_count": 3},
        {"id": 2, "path": "src/auth/user.py", "language": "python", "symbol_count": 2},
        {"id": 3, "path": "src/api.py", "language": "python", "symbol_count": 5},
        {"id": 4, "path": "README.md", "language": "markdown", "symbol_count": 0},
    ]
    tree = routes._build_file_tree(rows)

    # dirs before files, each group alphabetical
    assert [n["name"] for n in tree] == ["src", "README.md"]

    src = tree[0]
    assert src["type"] == "dir"
    assert src["file_count"] == 3 and src["symbol_count"] == 10  # rolled up from both levels
    assert [n["name"] for n in src["children"]] == ["auth", "api.py"]

    auth_dir = src["children"][0]
    assert auth_dir["path"] == "src/auth"           # full path, not just the segment
    assert auth_dir["file_count"] == 2 and auth_dir["symbol_count"] == 5
    assert [n["name"] for n in auth_dir["children"]] == ["login.py", "user.py"]
    assert all(n["type"] == "file" for n in auth_dir["children"])


def test_build_file_tree_handles_root_level_only():
    rows = [{"id": 1, "path": "main.py", "language": "python", "symbol_count": 2}]
    tree = routes._build_file_tree(rows)
    assert len(tree) == 1 and tree[0]["type"] == "file" and tree[0]["path"] == "main.py"


def test_tree_endpoint_returns_nested_by_default_and_flat_on_request(authed_client, make_repo):
    repo_path = make_repo({
        "src/auth/login.py": "def login():\n    return 1\n",
        "src/api.py": "def api():\n    return 2\n",
    })
    res = authed_client.post("/api/repos/index", json={"url": repo_path})
    repo_id = res.json()["repository_id"]

    nested = authed_client.get(f"/api/repos/{repo_id}/tree").json()
    assert [n["name"] for n in nested] == ["src"]
    assert nested[0]["type"] == "dir" and nested[0]["file_count"] == 2

    flat = authed_client.get(f"/api/repos/{repo_id}/tree?flat=true").json()
    assert sorted(f["path"] for f in flat) == ["src/api.py", "src/auth/login.py"]
    assert all("children" not in f for f in flat)


def test_tree_of_another_users_repo_is_404(authed_client, make_repo):
    repo_path = make_repo({"a.py": "def a():\n    return 1\n"})
    repo_id = authed_client.post("/api/repos/index", json={"url": repo_path}).json()["repository_id"]
    authed_client.post("/api/auth/logout")
    authed_client.post("/api/auth/signup", json={"email": "other@example.com", "password": "correct horse battery staple"})
    assert authed_client.get(f"/api/repos/{repo_id}/tree").status_code == 404


# ---------------------------------------------------------------------------
# Account module (round nine)
# ---------------------------------------------------------------------------

def test_account_overview_rolls_up_indexed_totals(authed_client, make_repo):
    repo_path = make_repo({"a.py": "def a():\n    return 1\n"})
    authed_client.post("/api/repos/index", json={"url": repo_path})

    acct = authed_client.get("/api/auth/account").json()
    assert acct["email"] == "apitester@example.com"
    assert acct["repo_count"] == 1
    assert acct["symbol_count"] >= 1
    assert "created_at" in acct
    assert "password_hash" not in acct  # never leak the hash


def test_account_overview_requires_auth():
    client = TestClient(app)
    assert client.get("/api/auth/account").status_code == 401


def test_change_email_updates_login_identity(authed_client):
    res = authed_client.post("/api/auth/change-email", json={
        "new_email": "moved@example.com",
        "current_password": "correct horse battery staple",
    })
    assert res.status_code == 200 and res.json()["email"] == "moved@example.com"

    authed_client.post("/api/auth/logout")
    # old address no longer works, new one does
    assert authed_client.post("/api/auth/login", json={"email": "apitester@example.com", "password": "correct horse battery staple"}).status_code == 401
    assert authed_client.post("/api/auth/login", json={"email": "moved@example.com", "password": "correct horse battery staple"}).status_code == 200


def test_change_email_requires_correct_password(authed_client):
    res = authed_client.post("/api/auth/change-email", json={
        "new_email": "moved@example.com", "current_password": "wrong password",
    })
    assert res.status_code == 400


def test_change_email_rejects_address_already_in_use(authed_client):
    authed_client.post("/api/auth/logout")
    authed_client.post("/api/auth/signup", json={"email": "taken@example.com", "password": "correct horse battery staple"})
    authed_client.post("/api/auth/logout")
    authed_client.post("/api/auth/login", json={"email": "apitester@example.com", "password": "correct horse battery staple"})

    res = authed_client.post("/api/auth/change-email", json={
        "new_email": "taken@example.com", "current_password": "correct horse battery staple",
    })
    assert res.status_code == 400


def test_delete_account_requires_typed_confirmation(authed_client):
    res = authed_client.post("/api/auth/delete-account", json={
        "current_password": "correct horse battery staple", "confirm": "yes",
    })
    assert res.status_code == 422  # pydantic validator rejects it before the handler


def test_delete_account_requires_correct_password(authed_client):
    res = authed_client.post("/api/auth/delete-account", json={
        "current_password": "wrong password", "confirm": "DELETE",
    })
    assert res.status_code == 400


def test_delete_account_removes_user_and_cascades_repos(authed_client, make_repo):
    repo_path = make_repo({"a.py": "def a():\n    return 1\n"})
    authed_client.post("/api/repos/index", json={"url": repo_path})

    res = authed_client.post("/api/auth/delete-account", json={
        "current_password": "correct horse battery staple", "confirm": "DELETE",
    })
    assert res.status_code == 200

    # session cleared, credentials gone, and the indexed data went with it
    assert authed_client.get("/api/auth/me").json() is None
    assert authed_client.post("/api/auth/login", json={"email": "apitester@example.com", "password": "correct horse battery staple"}).status_code == 401
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM repositories").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"] == 0
