"""
End-to-end tests for the full indexing pipeline (clone -> parse -> resolve
-> persist), run against small local git fixture repos rather than real
GitHub URLs so these run offline and fast. Each of these reproduces a
specific scenario from the README -- if one of these breaks, the
corresponding real-world bug most likely came back.
"""
from app.indexer.graph_builder import index_repository
from app.indexer import analysis
from app.db import get_conn


def _symbol_id(qualified_name: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM symbols WHERE qualified_name = ?", (qualified_name,)).fetchone()
        assert row is not None, f"symbol {qualified_name!r} was not indexed"
        return row["id"]


def test_draft_example_journey_python(make_repo, test_user):
    """Reproduces the project draft's section 12 example journey exactly:
    checkout() -> process_payment() -> validate_cart()/calculate_total()/
    charge_card(), with tests mapped to process_payment and checkout."""
    repo_path = make_repo({
        "app.py": """
def validate_cart():
    return True

def calculate_total():
    return 100

def process_payment():
    validate_cart()
    calculate_total()
    return charge_card()

def charge_card():
    return True

def checkout():
    process_payment()
    return True
""",
        "tests/test_app.py": """
def test_process_payment():
    assert True

def test_checkout():
    assert True
""",
    })

    result = index_repository(repo_path, user_id=test_user["id"])
    assert result["file_count"] == 2
    assert result["symbol_count"] == 7

    sid = _symbol_id("process_payment")
    callers = analysis.get_callers(sid)
    assert {c["qualified_name"] for c in callers} == {"checkout"}

    impact = analysis.impact_analysis(sid)
    assert {t["test_name"] for t in impact["affected_tests"]} == {"test_process_payment", "test_checkout"}


def test_local_shadowing_prevents_cross_module_false_positive(make_repo, test_user):
    """Reproduces the exact client/server name-collision bug: a client
    component destructures `signup` from a hook and calls it; a server
    controller separately defines its own unrelated top-level `signup`.
    Without local-binding tracking, impact analysis on a server helper
    would incorrectly pull in the client component."""
    repo_path = make_repo({
        "server/auth.js": """
function generateToken() { return "t"; }
function signup() { return generateToken(); }
function login() { return generateToken(); }
""",
        "client/Signup.jsx": """
function Signup() {
  const { signup } = useAuth();
  const handleSubmit = (e) => {
    signup(form);
  };
}
""",
        "client/AuthContext.jsx": """
export function useAuth() {
  return {};
}
""",
    })

    index_repository(repo_path, user_id=test_user["id"])
    sid = _symbol_id("generateToken")
    impact = analysis.impact_analysis(sid)
    affected_names = {s["qualified_name"] for s in impact["affected_symbols"]}
    assert affected_names == {"signup", "login"}  # only the real server-side callers
    assert "Signup" not in affected_names
    assert "Signup.handleSubmit" not in affected_names


def test_import_resolution_links_call_to_specific_file_not_repo_wide(make_repo, test_user):
    """A call resolves through its import to the specific file/symbol it
    came from, not by scanning every same-named symbol in the repo."""
    repo_path = make_repo({
        "client/services/api.js": """
export const signupRequest = (payload) => fetch("/signup", payload);
""",
        "client/context/AuthContext.jsx": """
import { signupRequest } from '../services/api';
export function AuthProvider() {
  const signup = async (payload) => {
    return signupRequest(payload);
  };
}
""",
        "server/authController.js": """
function signup() { return true; }  // unrelated same-named function elsewhere in the repo
""",
    })

    index_repository(repo_path, user_id=test_user["id"])
    sid = _symbol_id("signupRequest")
    callers = analysis.get_callers(sid)
    assert {c["qualified_name"] for c in callers} == {"AuthProvider.signup"}


def test_sibling_scope_isolation_does_not_suppress_real_calls(make_repo, test_user):
    """Reproduces the round-three false-negative: a name declared inside
    one nested function must not shadow a call of the same name made from
    a *sibling* nested function."""
    repo_path = make_repo({
        "a.js": """
export function Outer() {
  const helperA = () => {
    const secret = getSecret();
    return secret;
  };
  const helperB = () => {
    return secret();
  };
}
function secret() { return 42; }  // the real, unrelated top-level `secret`
""",
    })

    index_repository(repo_path, user_id=test_user["id"])
    sid = _symbol_id("secret")
    callers = analysis.get_callers(sid)
    # helperB's call to secret() must resolve to the real top-level `secret`,
    # not be silently dropped because of a bogus inherited shadow from its sibling helperA.
    assert {c["qualified_name"] for c in callers} == {"Outer.helperB"}


def test_external_import_is_never_guessed(make_repo, test_user):
    """A call to a name imported from outside the repo (a bare package
    specifier) must never fall back to a same-named repo symbol."""
    repo_path = make_repo({
        "a.js": """
import { useCallback } from 'react';
export function Component() {
  useCallback(() => {}, []);
}
""",
        "b.js": """
function useCallback() { return "not the real react hook"; }
""",
    })

    index_repository(repo_path, user_id=test_user["id"])
    sid = _symbol_id("useCallback")  # the repo-local decoy, not react's
    callers = analysis.get_callers(sid)
    assert callers == []  # Component's call must NOT resolve here


def test_ambiguous_names_above_threshold_are_left_unresolved(make_repo, test_user):
    """Regression: stress-testing against Django (3,000+ files) found that
    a name like `__init__`, shared by hundreds of unrelated classes, turned
    every bare call into an edge to *all* of them -- 1M+ dependency edges
    for 38k symbols. A name with more than AMBIGUOUS_NAME_THRESHOLD
    same-named candidates repo-wide must be left unresolved by the
    repo-wide fallback rather than linked to all of them."""
    files = {}
    # 12 unrelated classes, each with its own same-named `overloaded` method --
    # deliberately over the threshold (10)
    for i in range(12):
        files[f"mod{i}.py"] = f"class C{i}:\n    def overloaded(self):\n        return {i}\n"
    files["caller.py"] = "def caller():\n    overloaded()\n"
    repo_path = make_repo(files)

    index_repository(repo_path, user_id=test_user["id"])

    sid = _symbol_id("caller")
    callees = analysis.get_callees(sid)
    assert callees == [], "a name with 12 candidates must not resolve via fallback at all"


def test_small_scale_ambiguity_still_resolves_via_fallback(make_repo, test_user):
    """The threshold must not suppress genuine small-scale name reuse --
    only pathological cases (see test above) get left unresolved."""
    repo_path = make_repo({
        "small_a.py": "def shared():\n    return 1\n",
        "small_b.py": "def shared():\n    return 2\n",
        "caller.py": "def caller():\n    shared()\n",
    })
    index_repository(repo_path, user_id=test_user["id"])
    sid = _symbol_id("caller")
    callees = analysis.get_callees(sid)
    assert len(callees) == 2  # both `shared` definitions, since 2 is well under the threshold
    assert all(c["qualified_name"] == "shared" for c in callees)
    assert {c["path"] for c in callees} == {"small_a.py", "small_b.py"}


def test_reindexing_same_url_replaces_rather_than_duplicates(make_repo, test_user):
    repo_path = make_repo({"a.py": "def foo():\n    return 1\n"})
    r1 = index_repository(repo_path, user_id=test_user["id"])
    r2 = index_repository(repo_path, user_id=test_user["id"])
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM repositories WHERE url = ?", (repo_path,)).fetchall()
    assert len(rows) == 1
    assert r2["repository_id"] != r1["repository_id"]  # old row deleted, new one inserted


def test_attribute_call_resolves_to_specific_class_not_every_namesake(make_repo, test_user):
    """The Django `filter()` case from round four: several unrelated
    classes each define a method with the same name. Before receiver-type
    inference, `some_queryset.filter(...)` resolved to *every* `filter`
    repo-wide (name-only fallback). Now that the receiver's type is known
    (`qs = QuerySet()`), it should resolve to exactly QuerySet.filter."""
    repo_path = make_repo({
        "orm.py": "class QuerySet:\n    def filter(self, **kwargs):\n        return self\n",
        "logging_mod.py": "class Logger:\n    def filter(self, record):\n        return True\n",
        "templates.py": "class TemplateFilter:\n    def filter(self, value):\n        return value\n",
        "views.py": (
            "from orm import QuerySet\n\n"
            "def get_active_users():\n"
            "    qs = QuerySet()\n"
            "    return qs.filter(active=True)\n"
        ),
    })
    index_repository(repo_path, user_id=test_user["id"])

    sid = _symbol_id("get_active_users")
    callee_names = {c["qualified_name"] for c in analysis.get_callees(sid)}
    assert "QuerySet.filter" in callee_names
    assert "Logger.filter" not in callee_names
    assert "TemplateFilter.filter" not in callee_names


def test_self_call_resolves_to_own_class_method_not_other_classes(make_repo, test_user):
    """`self.method()` should resolve to *this* class's method specifically,
    not every same-named method across unrelated classes."""
    repo_path = make_repo({
        "a.py": (
            "class UserService:\n"
            "    def notify(self):\n"
            "        self.send()\n\n"
            "    def send(self):\n"
            "        return True\n"
        ),
        "b.py": "class MailService:\n    def send(self):\n        return True\n",
    })
    index_repository(repo_path, user_id=test_user["id"])

    sid = _symbol_id("UserService.notify")
    callees = analysis.get_callees(sid)
    assert [c["qualified_name"] for c in callees] == ["UserService.send"]


def test_typed_call_with_no_matching_symbol_stays_unresolved_not_guessed(make_repo, test_user):
    """If the receiver's type is known but no matching class/method exists
    in the repo (e.g. the class comes from an external library, or the
    heuristic guessed a name that isn't real), the call is left
    unresolved rather than falling back to a repo-wide name guess --
    same 'known-unresolvable beats a wrong guess' posture as external
    imports and over-threshold ambiguity."""
    repo_path = make_repo({
        "unrelated.py": "def somewhere():\n    return True\n",
        "a.py": (
            "def make_request():\n"
            "    client = ExternalClient()\n"
            "    return client.somewhere()\n"
        ),
    })
    index_repository(repo_path, user_id=test_user["id"])

    sid = _symbol_id("make_request")
    callees = analysis.get_callees(sid)
    assert callees == []


def test_attribute_call_js_resolves_to_specific_class(make_repo, test_user):
    repo_path = make_repo({
        "orm.js": "export class QuerySet {\n  filter(active) {\n    return this;\n  }\n}\n",
        "log.js": "export class Logger {\n  filter(record) {\n    return true;\n  }\n}\n",
        "views.js": (
            "import { QuerySet } from './orm';\n\n"
            "function getActiveUsers() {\n"
            "  const qs = new QuerySet();\n"
            "  return qs.filter(true);\n"
            "}\n"
        ),
    })
    index_repository(repo_path, user_id=test_user["id"])

    sid = _symbol_id("getActiveUsers")
    callee_names = {c["qualified_name"] for c in analysis.get_callees(sid)}
    assert "QuerySet.filter" in callee_names
    assert "Logger.filter" not in callee_names
