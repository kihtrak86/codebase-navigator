"""
Second pass: takes all ExtractedFile results for a repository, resolves call
names to actual symbol ids, and writes repositories/files/symbols/
dependencies/tests into SQLite.

Resolution strategy, in priority order, for each call name inside a symbol:
  1. If the name is one of that symbol's own local_bindings (a parameter,
     or bound by a const/let/var declaration or destructuring inside its
     body), it refers to a local value, not a repo symbol. No edge at all
     -- this is what stops `const { signup } = useAuth(); signup(...)`
     from ever being considered.
  2. Else if the name was imported in that file, resolve through
     import_resolver.py to the specific file it came from and look for a
     top-level symbol there with the imported (original) name. If the
     import points outside the repo (a package like 'react', a python
     stdlib module), the call is deliberately left unresolved -- we know
     it isn't a repo symbol, so guessing would be worse than nothing.
  3. Otherwise (not imported, not locally bound -- e.g. a same-file
     sibling function, or a genuinely global/ambient name) fall back to
     repo-wide name matching, same as before -- but only when the name
     isn't wildly ambiguous (see AMBIGUOUS_NAME_THRESHOLD below). This is
     still false-positive-tolerant by design for genuine small-scale name
     reuse (see import_resolver.py's docstring), but steps 1-2 now remove
     the two biggest sources of wrong matches: local shadowing and
     cross-module name reuse.

Separately, *typed* calls (attribute calls where the receiver's type was
inferred -- see each parser's `typed_calls`, e.g. `self.foo()` inside
class `Bar`, or `x = PaymentGateway(); x.charge()`) skip all three steps
above entirely and are resolved directly against that specific class's
method (`Bar.foo`, `PaymentGateway.charge`) via qualified-name lookup,
capped by the same ambiguity threshold. This is what disambiguates
`some_queryset.filter(...)` from `some_logger.filter(...)` when the
receiver's type is known -- see "Round seven" in the README for the
concrete Django case this fixes and what it still doesn't.
"""
import os
from . import clone
from .python_parser import parse_python_file
from .js_parser import parse_js_file
from .import_resolver import build_import_maps
from ..db import get_conn

# Stress-testing against a large real repo (Django, ~3,000 Python files)
# found that fallback matching alone produced over a million dependency
# edges for ~38,000 symbols -- because a name like `__init__` (880 distinct
# methods repo-wide) turns every bare `self.__init__(...)` / `super().
# __init__(...)` call into an edge to *every* `__init__` in the codebase.
# That's not "tolerant of some false positives" anymore, it's a graph that's
# useless for its actual purpose: impact analysis on anything would show
# most of the codebase as "affected." A name with more candidates than this
# threshold is left unresolved by the fallback rather than guessed -- the
# same "known unresolvable is more honest than a guess" stance already
# taken for external imports. The threshold (10) comes from the real
# distribution on that repo: the overwhelming majority of names are either
# unique (25,137) or have a handful of legitimate duplicates (2,046 have
# 2-3, 510 have 4-8) -- collisions above ~10 are overwhelmingly generic/
# dunder-style names, not genuine repo-specific reuse worth linking.
AMBIGUOUS_NAME_THRESHOLD = 10

JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
SUPPORTED_EXTENSIONS = (".py",) + JS_EXTENSIONS


def _parse_file(source: str, relpath: str):
    if relpath.endswith(".py"):
        return parse_python_file(source, relpath)
    if relpath.endswith(JS_EXTENSIONS):
        return parse_js_file(source, relpath)
    return None


def index_repository(url: str, user_id: int) -> dict:
    local_path, commit_hash = clone.clone_repo(url)
    try:
        return _index_local_repo(url, local_path, commit_hash, user_id)
    finally:
        clone.cleanup_repo(local_path)


def _index_local_repo(url: str, local_path: str, commit_hash: str, user_id: int) -> dict:
    repo_name = url.rstrip("/").split("/")[-1].removesuffix(".git")

    extracted_files = []
    for abspath, relpath in clone.iter_source_files(local_path, extensions=SUPPORTED_EXTENSIONS):
        try:
            with open(abspath, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
        except OSError:
            continue
        parsed = _parse_file(source, relpath.replace(os.sep, "/"))
        if parsed is not None:
            extracted_files.append(parsed)

    import_maps = build_import_maps(extracted_files)

    with get_conn() as conn:
        # Re-indexing a URL that's already in the DB replaces it rather than
        # accumulating duplicate repository rows (cascades to its files/
        # symbols/dependencies/tests via the schema's ON DELETE CASCADE).
        # Scoped per-user: two different users indexing the same public repo
        # get independent copies, not a shared row one of them can delete.
        for row in conn.execute(
            "SELECT id FROM repositories WHERE url = ? AND user_id = ?", (url, user_id)
        ).fetchall():
            conn.execute("DELETE FROM repositories WHERE id = ?", (row["id"],))

        cur = conn.execute(
            "INSERT INTO repositories (user_id, name, url, commit_hash) VALUES (?, ?, ?, ?)",
            (user_id, repo_name, url, commit_hash),
        )
        repository_id = cur.lastrowid

        file_id_by_path: dict[str, int] = {}
        symbol_id_by_simple: dict[str, list[int]] = {}          # simple name -> [symbol_id], repo-wide
        symbols_by_file_and_name: dict[tuple[str, str], list[int]] = {}  # (relpath, name) -> [symbol_id], top-level only
        symbol_id_by_qualified: dict[str, list[int]] = {}        # qualified_name -> [symbol_id], repo-wide -- used
                                                                    # for typed-call resolution ("ClassName.method")
        pending_calls: list[tuple[int, list[str], list[tuple[str, str]], str, set[str]]] = []
        # (symbol_id, calls, typed_calls, file_relpath, local_bindings)
        pending_tests: list[tuple[int, int]] = []  # (test_symbol_id, file_id)

        for ef in extracted_files:
            fcur = conn.execute(
                "INSERT INTO files (repository_id, path, language) VALUES (?, ?, ?)",
                (repository_id, ef.relpath, ef.language),
            )
            file_id = fcur.lastrowid
            file_id_by_path[ef.relpath] = file_id

            parent_id_by_qname: dict[str, int] = {}

            for sym in ef.symbols:
                parent_id = parent_id_by_qname.get(sym.parent_qualified_name) if sym.parent_qualified_name else None
                scur = conn.execute(
                    """INSERT INTO symbols
                       (file_id, repository_id, name, qualified_name, type, start_line, end_line, parent_symbol_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (file_id, repository_id, sym.name, sym.qualified_name, sym.type,
                     sym.start_line, sym.end_line, parent_id),
                )
                symbol_id = scur.lastrowid
                # Register every symbol (not just classes) as a potential parent --
                # this is what lets a nested function's own qualified_name resolve
                # correctly when *its* nested functions look up parent_symbol_id.
                parent_id_by_qname[sym.qualified_name] = symbol_id

                symbol_id_by_simple.setdefault(sym.name, []).append(symbol_id)
                symbol_id_by_qualified.setdefault(sym.qualified_name, []).append(symbol_id)
                if parent_id is None:  # top-level: what an import can actually bind to
                    symbols_by_file_and_name.setdefault((ef.relpath, sym.name), []).append(symbol_id)

                if sym.calls or sym.typed_calls:
                    pending_calls.append((symbol_id, sym.calls, sym.typed_calls, ef.relpath, sym.local_bindings))
                if sym.is_test:
                    pending_tests.append((symbol_id, file_id))

        dependency_rows = []
        for source_symbol_id, call_names, typed_calls, file_relpath, local_bindings in pending_calls:
            seen_targets = set()
            file_import_map = import_maps.get(file_relpath, {})

            # Typed calls first: resolved directly against the specific
            # class's method (e.g. "PaymentGateway.charge"), bypassing
            # local-binding/import/fallback matching entirely -- a known
            # receiver type is more specific than any of those three
            # signals, and mixing it with the name-only fallback below
            # would just reintroduce the ambiguity this exists to remove.
            for receiver_type, method_name in typed_calls:
                qualified_key = f"{receiver_type}.{method_name}"
                targets = symbol_id_by_qualified.get(qualified_key, [])
                if len(targets) > AMBIGUOUS_NAME_THRESHOLD:
                    continue  # e.g. two unrelated classes both literally named receiver_type
                for target_id in targets:
                    if target_id == source_symbol_id or target_id in seen_targets:
                        continue
                    seen_targets.add(target_id)
                    dependency_rows.append((repository_id, source_symbol_id, target_id, "calls"))

            for name in call_names:
                if name in local_bindings:
                    continue  # step 1: shadowed by a param/local declaration -- not a repo symbol

                resolution = file_import_map.get(name)
                if resolution is not None:
                    if resolution[0] == "file":
                        _, target_relpath, orig_name = resolution
                        lookup_name = orig_name or name  # default import: best guess is the local alias itself
                        targets = symbols_by_file_and_name.get((target_relpath, lookup_name), [])
                        for target_id in targets:
                            if target_id == source_symbol_id or target_id in seen_targets:
                                continue
                            seen_targets.add(target_id)
                            dependency_rows.append((repository_id, source_symbol_id, target_id, "calls"))
                    # resolution[0] == "external": known to come from outside the repo -- no edge, no fallback
                    continue

                # step 3: not imported, not locally bound -- repo-wide name fallback,
                # skipped entirely when the name is too ambiguous to mean anything
                # (see AMBIGUOUS_NAME_THRESHOLD above)
                fallback_targets = symbol_id_by_simple.get(name, [])
                if len(fallback_targets) > AMBIGUOUS_NAME_THRESHOLD:
                    continue
                for target_id in fallback_targets:
                    if target_id == source_symbol_id or target_id in seen_targets:
                        continue
                    seen_targets.add(target_id)
                    dependency_rows.append((repository_id, source_symbol_id, target_id, "calls"))

        if dependency_rows:
            conn.executemany(
                """INSERT INTO dependencies (repository_id, source_symbol_id, target_symbol_id, relationship_type)
                   VALUES (?, ?, ?, ?)""",
                dependency_rows,
            )

        # best-effort test -> covered-symbol mapping: a test named test_foo
        # is assumed to cover a symbol named foo, if one exists.
        test_rows = []
        for test_symbol_id, file_id in pending_tests:
            row = conn.execute("SELECT name FROM symbols WHERE id = ?", (test_symbol_id,)).fetchone()
            covers_id = None
            if row and row["name"].startswith("test_"):
                target_name = row["name"][len("test_"):]
                candidates = symbol_id_by_simple.get(target_name, [])
                covers_id = candidates[0] if candidates else None
            test_rows.append((file_id, test_symbol_id, covers_id, "unit"))

        if test_rows:
            conn.executemany(
                "INSERT INTO tests (file_id, symbol_id, covers_symbol_id, test_type) VALUES (?, ?, ?, ?)",
                test_rows,
            )

        file_count = len(extracted_files)
        symbol_count = sum(len(ef.symbols) for ef in extracted_files)
        dep_count = len(dependency_rows)

        conn.execute(
            "UPDATE repositories SET file_count=?, symbol_count=?, dependency_count=? WHERE id=?",
            (file_count, symbol_count, dep_count, repository_id),
        )

        return {
            "repository_id": repository_id,
            "name": repo_name,
            "url": url,
            "commit_hash": commit_hash,
            "file_count": file_count,
            "symbol_count": symbol_count,
            "dependency_count": dep_count,
        }
