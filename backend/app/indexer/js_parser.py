"""
Heuristic parser for JS/JSX/TS/TSX. There's no `ast` module for these
languages in the standard library, and pulling in a real parser (Tree-sitter,
Babel, TypeScript's compiler API) is exactly the upgrade the README flags as
the first thing worth doing properly. Until then, this uses masking +
brace-counting, which is deliberately conservative about what it claims to
extract: it covers function declarations, `const x = (...) => {}` / `const x
= (...) => expr` (block-bodied and concise arrows both), `function`
expressions, and classes with their methods -- the overwhelming majority of
real-world JS/TS/React code.

Three things beyond plain extraction, added incrementally as testing against
a real repo surfaced concrete problems:

  1. Structured imports (ImportBinding: local name -> module + original
     name), so a call can be resolved to the *specific file* it was
     imported from instead of matched by name across the whole repo.
  2. Local-binding tracking: names bound inside a function by a plain
     `const`/`let`/`var` declaration (including destructuring) or by its
     own parameters. A call to one of these names is a call to that local
     value, not to some same-named symbol elsewhere in the repo -- e.g.
     `const { signup } = useAuth(); ... signup(...)` must never resolve to
     an unrelated top-level `signup` function in a completely different
     file. graph_builder.py treats local_bindings as a hard stop: no
     fallback name search for those names, resolved or not.
  3. Nested named functions: a function declared inside another function
     (`const signup = useCallback(...)` inside a component) is extracted as
     its own symbol, parented to the enclosing one (`AuthProvider.signup`),
     at any depth -- a function nested inside a nested function is handled
     the same way, recursively. Its calls are attributed to it
     specifically, not folded into the enclosing symbol.
  4. Scope-correct local-binding inheritance: a name shadowed by an
     enclosing function (case 2, above) still shadows calls made from
     inside a closure over it, at any nesting depth -- but only downward
     through actual enclosing scopes, never sideways between sibling
     nested functions or upward from a child into its parent. Getting this
     boundary exactly right took two attempts: the first version computed
     a scope's "own" bindings from its full body text, which included
     text that actually belonged to a nested child -- so a name declared
     inside one nested function leaked into its *siblings* as if it were
     shared. See `_build_block_symbol`'s docstring for the concrete
     example and the fix (detect nested regions and blank them out
     *before* scanning for what a scope actually declares, not after).

Known gaps (documented rather than silently wrong):
  - Multi-line function signatures (params spanning multiple lines) are not
    detected -- only single-line `name(...) {` / `= (...) => {` signatures.
  - Concise arrow bodies are only detected when the whole expression is on
    one line.
  - Calls made inside template-literal interpolations (`` `${foo()}` ``) are
    not seen, since template literals are masked out like any other string.
  - Object-literal methods (`{ foo() { ... } }`) are not extracted as
    symbols.
  - JSX markup itself is not parsed as structure -- a component's JSX return
    value is just body text to this parser, not a tree.
  - A named function declared inside a nested function *at the same
    physical line as a sibling's own declaration* could theoretically
    still confuse detection in pathological one-line-per-statement-free
    code, but ordinary formatting (each declaration on its own line) is
    unaffected. Anonymous inline callbacks (`useEffect(() => {...})`) are
    never extracted as symbols regardless of depth, since they have no
    name to key a symbol on -- their calls fold into whichever named
    symbol encloses them.
These match the same "false-negative over false-positive" posture as the
Python call resolver: better to miss an edge case than fabricate structure.
"""
import re
from .common import ExtractedSymbol, ExtractedFile, ImportBinding

_JS_KEYWORDS_NOT_CALLS = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "new", "do", "else", "try", "finally", "yield", "await", "in", "of",
    "delete", "void", "instanceof", "with", "async",
}

_IMPORT_NAMESPACE_RE = re.compile(
    r"""import\s+\*\s*as\s+([A-Za-z_$][\w$]*)\s+from\s*["']([^"']+)["']"""
)
_IMPORT_DEFAULT_NAMED_RE = re.compile(
    r"""import\s+(?:type\s+)?"""
    r"""(?:([A-Za-z_$][\w$]*)\s*,\s*)?"""          # 1: default name, only when followed by a comma
    r"""(?:\{([^}]*)\}|([A-Za-z_$][\w$]*))?"""     # 2: named-imports blob  OR  3: bare default name
    r"""\s*from\s*["']([^"']+)["']"""               # 4: module specifier
)
_REQUIRE_DESTRUCTURE_RE = re.compile(
    r"""(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\(\s*["']([^"']+)["']\s*\)"""
)
_REQUIRE_DEFAULT_RE = re.compile(
    r"""(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*["']([^"']+)["']\s*\)"""
)

_FUNC_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"
)
_CONST_ARROW_BLOCK_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:[A-Za-z_$][\w$]*\(\s*)?"   # optional wrapping call, e.g. useCallback( / useMemo(
    r"(?:async\s+)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{"
)
_CONST_ARROW_CONCISE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:[A-Za-z_$][\w$]*\(\s*)?"   # optional wrapping call, e.g. useCallback( / useMemo(
    r"(?:async\s+)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*(?!\{)(.+)$"
)
_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"
)
_METHOD_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?"
    r"(\*?[A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*(?::\s*[^{]+)?\{"
)
_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_ATTR_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\s*\(")
_NEW_ASSIGN_RE = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*new\s+([A-Za-z_$][\w$]*)\s*\("
)
_LOCAL_DECL_RE = re.compile(r"\b(?:const|let|var)\s+(\{[^{}]*\}|\[[^\[\]]*\]|[A-Za-z_$][\w$]*)\s*=")


def _split_top_level_commas(s: str) -> list[str]:
    parts, current, depth = [], [], 0
    for ch in s:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


_IDENT_RE = re.compile(r"^[A-Za-z_$][\w$]*$")


def _names_from_binding_target(target: str) -> list[str]:
    """Extracts the locally-bound identifier(s) from a declaration/param target:
    a plain name, `{a, b: c, ...rest}`, `[a, , c]`, with optional `= default`."""
    target = target.strip()
    if not target:
        return []
    if target.startswith("{") :
        inner = target[1:-1] if target.endswith("}") else target[1:]
        names = []
        for part in _split_top_level_commas(inner):
            part = part.strip()
            if part.startswith("..."):
                part = part[3:].strip()
            if not part:
                continue
            if ":" in part:
                part = part.split(":", 1)[1]
            if "=" in part:
                part = part.split("=", 1)[0]
            part = part.strip()
            if _IDENT_RE.match(part):
                names.append(part)
        return names
    if target.startswith("["):
        inner = target[1:-1] if target.endswith("]") else target[1:]
        names = []
        for part in _split_top_level_commas(inner):
            part = part.strip()
            if part.startswith("..."):
                part = part[3:].strip()
            if "=" in part:
                part = part.split("=", 1)[0].strip()
            if _IDENT_RE.match(part):
                names.append(part)
        return names
    if target.startswith("..."):
        target = target[3:].strip()
    if "=" in target:
        target = target.split("=", 1)[0].strip()
    return [target] if _IDENT_RE.match(target) else []


def _parse_import_named_list(blob: str) -> list[tuple[str, str]]:
    """'foo, bar as baz' -> [('foo','foo'), ('bar','baz')]"""
    pairs = []
    for part in _split_top_level_commas(blob):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            orig, local = part.split(" as ", 1)
            pairs.append((orig.strip(), local.strip()))
        else:
            pairs.append((part, part))
    return pairs


def _parse_destructure_pairs(blob: str) -> list[tuple[str, str]]:
    """'{ a, b: c }' inner blob -> [('a','a'), ('b','c')], require()-destructure style."""
    pairs = []
    for part in _split_top_level_commas(blob):
        part = part.strip()
        if part.startswith("..."):
            part = part[3:].strip()
        if not part:
            continue
        if "=" in part:
            part = part.split("=", 1)[0].strip()
        if ":" in part:
            orig, local = part.split(":", 1)
            pairs.append((orig.strip(), local.strip()))
        else:
            pairs.append((part, part))
    return pairs


def _parse_imports(source: str) -> list[ImportBinding]:
    imports = []
    for m in _IMPORT_NAMESPACE_RE.finditer(source):
        imports.append(ImportBinding(local_name=m.group(1), module=m.group(2), orig_name="*"))
    for m in _IMPORT_DEFAULT_NAMED_RE.finditer(source):
        default_name, named_blob, bare_default, module = m.group(1), m.group(2), m.group(3), m.group(4)
        if default_name:
            imports.append(ImportBinding(local_name=default_name, module=module, orig_name=None))
        if bare_default:
            imports.append(ImportBinding(local_name=bare_default, module=module, orig_name=None))
        if named_blob is not None:
            for orig, local in _parse_import_named_list(named_blob):
                imports.append(ImportBinding(local_name=local, module=module, orig_name=orig))
    for m in _REQUIRE_DESTRUCTURE_RE.finditer(source):
        for orig, local in _parse_destructure_pairs(m.group(1)):
            imports.append(ImportBinding(local_name=local, module=m.group(2), orig_name=orig))
    for m in _REQUIRE_DEFAULT_RE.finditer(source):
        imports.append(ImportBinding(local_name=m.group(1), module=m.group(2), orig_name=None))
    return imports


def _mask_non_code(source: str) -> str:
    """Replace string/template-literal contents and comments with spaces,
    preserving newlines and exact character offsets, so brace-counting and
    signature regexes aren't confused by braces or keywords inside them."""
    out = []
    i, n = 0, len(source)
    state = None  # None | 'line_comment' | 'block_comment' | 'string'
    quote = None
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if state == "line_comment":
            if c == "\n":
                out.append("\n")
                state = None
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block_comment":
            if c == "*" and nxt == "/":
                out.append("  ")
                i += 2
                state = None
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if state == "string":
            if c == "\\":
                out.append(" ")
                i += 1
                if i < n:
                    out.append("\n" if source[i] == "\n" else " ")
                    i += 1
                continue
            if c == quote:
                out.append(" ")
                state = None
                quote = None
                i += 1
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        # normal code
        if c == "/" and nxt == "/":
            out.append("  ")
            i += 2
            state = "line_comment"
            continue
        if c == "/" and nxt == "*":
            out.append("  ")
            i += 2
            state = "block_comment"
            continue
        if c in ("'", '"', "`"):
            quote = c
            out.append(" ")
            state = "string"
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _find_block_end(masked_lines: list[str], start_idx: int) -> int:
    """Given the line where a `{` was opened (start_idx), returns the index
    of the line where the matching `}` closes it. Assumes the block actually
    opens on start_idx (caller checks for '{' presence first)."""
    depth = 0
    for idx in range(start_idx, len(masked_lines)):
        depth += masked_lines[idx].count("{") - masked_lines[idx].count("}")
        if depth <= 0:
            return idx
    return len(masked_lines) - 1


def _extract_calls(masked_body_lines: list[str], enclosing_class: str | None = None) -> tuple[list[str], list[tuple[str, str]]]:
    """Returns (calls, typed_calls). `calls` is exactly what this returned
    before: every call name seen, bare (`foo(`) or attribute (`x.foo(`)
    alike, receiver ignored. `typed_calls` is new: attribute calls whose
    receiver's type could be guessed, as (receiver_type, method_name) --
    `this.foo()` inside a class becomes (class_name, "foo"); `const x = new
    ClassName(...)` followed later by `x.foo()` becomes ("ClassName",
    "foo"). A call that lands in `typed_calls` is never also in `calls` --
    graph_builder resolves it against that specific class's method instead
    of every same-named method repo-wide.

    Same best-effort posture as the rest of this parser: a single linear
    pass in source order (a reassignment overwrites the tracked type for
    everything after it; no control-flow awareness), and only one level of
    `receiver.method(` is inspected -- `a.b.foo()` types the call using
    `b`, not `a`, same as the Python parser only looking at a direct Name
    receiver and not a chained Attribute."""
    calls: list[str] = []
    typed_calls: list[tuple[str, str]] = []
    var_types: dict[str, str] = {}

    for line in masked_body_lines:
        for m in _NEW_ASSIGN_RE.finditer(line):
            var_types[m.group(1)] = m.group(2)

        attr_spans = []
        for m in _ATTR_CALL_RE.finditer(line):
            receiver, method = m.group(1), m.group(2)
            if method in _JS_KEYWORDS_NOT_CALLS:
                continue
            attr_spans.append((m.start(2), m.end(2)))
            receiver_type = None
            if receiver == "this" and enclosing_class:
                receiver_type = enclosing_class
            elif receiver in var_types:
                receiver_type = var_types[receiver]
            if receiver_type:
                typed_calls.append((receiver_type, method))
            else:
                calls.append(method)

        for m in _CALL_RE.finditer(line):
            name = m.group(1).lstrip("*")
            if not name or name in _JS_KEYWORDS_NOT_CALLS:
                continue
            if any(start <= m.start(1) < end for start, end in attr_spans):
                continue  # already handled (typed or not) by the attribute-call pass above
            calls.append(name)

    return calls, typed_calls


def _extract_local_bindings(param_blob: str, masked_body_lines: list[str]) -> set[str]:
    names = set()
    if param_blob:
        inner = param_blob.strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1]
        for part in _split_top_level_commas(inner):
            names.update(_names_from_binding_target(part))
    for line in masked_body_lines:
        for m in _LOCAL_DECL_RE.finditer(line):
            names.update(_names_from_binding_target(m.group(1)))
    return names


def _find_nested_regions(masked_lines: list[str], body_start: int, body_end: int) -> list[dict]:
    """Detects named function declarations directly inside a block body
    (one syntactic level -- what's nested inside *those* is found by
    recursing on them separately, not here). Pure detection: returns line
    ranges and names only, with no notion of scope/bindings yet, since
    finding where a nested function starts and ends doesn't depend on what
    names are shadowed -- that separation is what let the leak below get
    fixed without rewriting detection at all."""
    regions = []
    if body_end <= body_start:
        return regions
    depth = masked_lines[body_start].count("{") - masked_lines[body_start].count("}")
    j = body_start + 1
    while j <= body_end:
        line = masked_lines[j]
        if depth == 1:
            func_match = _FUNC_DECL_RE.match(line)
            arrow_block_match = _CONST_ARROW_BLOCK_RE.match(line) if not func_match else None
            arrow_concise_match = (
                _CONST_ARROW_CONCISE_RE.match(line) if not (func_match or arrow_block_match) else None
            )

            if func_match and "{" in line:
                nested_end = _find_block_end(masked_lines, j)
                regions.append({"kind": "block", "name": func_match.group(1), "start": j, "end": nested_end,
                                 "param_blob": func_match.group(2)})
                j = nested_end + 1
                continue  # depth unchanged: skipped body is balanced

            if arrow_block_match:
                nested_end = _find_block_end(masked_lines, j)
                regions.append({"kind": "block", "name": arrow_block_match.group(1), "start": j, "end": nested_end,
                                 "param_blob": arrow_block_match.group(2)})
                j = nested_end + 1
                continue

            if arrow_concise_match:
                regions.append({"kind": "concise", "name": arrow_concise_match.group(1), "start": j, "end": j,
                                 "param_blob": arrow_concise_match.group(2), "expr_line": arrow_concise_match.group(3)})
                j += 1
                continue
        depth += line.count("{") - line.count("}")
        j += 1
    return regions


def _blank_regions(lines: list[str], regions: list[dict], offset: int) -> list[str]:
    out = list(lines)
    for r in regions:
        rel_start, rel_end = r["start"] - offset, r["end"] - offset
        for k in range(max(rel_start, 0), min(rel_end, len(out) - 1) + 1):
            out[k] = ""
    return out


def _build_block_symbol(
    masked_lines: list[str], start_idx: int, end_idx: int, name: str, qname: str, sym_type: str,
    parent_qname: str | None, param_blob: str, file_is_test: bool,
    inherited_bindings: frozenset = frozenset(),
    enclosing_class: str | None = None,
) -> tuple[ExtractedSymbol, list[ExtractedSymbol]]:
    """Builds one ExtractedSymbol for a block-bodied function/method/nested-
    function, plus every named function nested inside it, at any depth
    (each nested function is built via a recursive call to this same
    function, so `Outer.middle.inner` three levels deep works the same way
    `Outer.middle` does one level deep).

    Nested regions are detected first (_find_nested_regions) and blanked
    out of a working copy of the body *before* that copy is used for
    anything else. This one change fixes two separate things at once:

      - This symbol's own *calls* no longer include calls made only inside
        a nested closure (already true before this rewrite).
      - This symbol's own *declared bindings* -- what it's actually
        responsible for shadowing for whatever's nested inside it -- no
        longer include names declared *inside* one of its nested children.
        Before this fix, a name declared inside `helperA`'s own body
        (e.g. `const secret = ...`) leaked into `Outer`'s own scope_bindings
        (since it was scanned from the *unblanked* body), and from there
        into every sibling built from that same scope_bindings -- so
        `helperB` inherited `secret` as shadowed even though nothing in
        `helperB` or any scope enclosing it actually declares it. That's a
        false-negative bug: a genuine call to some unrelated `secret()`
        elsewhere in the repo would have been wrongly suppressed instead of
        resolved. Blanking nested regions before the own-bindings scan
        means only what's declared *directly* in a scope is attributed to
        that scope, and inheritance downward is the only way a name crosses
        a function boundary -- never sideways between siblings or upward
        from a child.
    """
    full_body = list(masked_lines[start_idx:end_idx + 1])
    brace_pos = full_body[0].find("{")
    full_body[0] = full_body[0][brace_pos + 1:] if brace_pos != -1 else ""

    regions = _find_nested_regions(masked_lines, start_idx, end_idx)
    working = _blank_regions(full_body, regions, start_idx)

    own_bindings = _extract_local_bindings(param_blob, working)
    scope_bindings = own_bindings | inherited_bindings

    nested: list[ExtractedSymbol] = []
    for r in regions:
        child_qname = f"{qname}.{r['name']}"
        if r["kind"] == "block":
            sub_symbol, grandchildren = _build_block_symbol(
                masked_lines, r["start"], r["end"], r["name"], child_qname, "function",
                qname, r["param_blob"], file_is_test, scope_bindings, enclosing_class,
            )
            nested.append(sub_symbol)
            nested.extend(grandchildren)
        else:  # concise: no body to recurse into further
            r_calls, r_typed_calls = _extract_calls([r["expr_line"]], enclosing_class)
            nested.append(ExtractedSymbol(
                name=r["name"], qualified_name=child_qname, type="function",
                start_line=r["start"] + 1, end_line=r["end"] + 1, parent_qualified_name=qname,
                calls=r_calls, typed_calls=r_typed_calls,
                is_test=file_is_test or r["name"].lower().startswith("test"),
                local_bindings=_extract_local_bindings(r["param_blob"], []) | scope_bindings,
            ))

    own_calls, own_typed_calls = _extract_calls(working, enclosing_class)
    symbol = ExtractedSymbol(
        name=name, qualified_name=qname, type=sym_type,
        start_line=start_idx + 1, end_line=end_idx + 1,
        parent_qualified_name=parent_qname,
        calls=own_calls, typed_calls=own_typed_calls,
        is_test=file_is_test or name.lower().startswith("test"),
        local_bindings=scope_bindings,
    )
    return symbol, nested


def _is_test_file(relpath: str) -> bool:
    p = relpath.replace("\\", "/").lower()
    return (
        ".test." in p or ".spec." in p or "/tests/" in p or "/__tests__/" in p
        or p.startswith("test_") or "/test/" in p
    )


def parse_js_file(source: str, relpath: str) -> ExtractedFile:
    language = "typescript" if relpath.endswith((".ts", ".tsx")) else "javascript"
    result = ExtractedFile(relpath=relpath, language=language)
    result.imports = _parse_imports(source)

    masked = _mask_non_code(source)
    masked_lines = masked.split("\n")
    file_is_test = _is_test_file(relpath)

    depth = 0
    i = 0
    n_lines = len(masked_lines)
    while i < n_lines:
        line = masked_lines[i]

        if depth == 0:
            class_match = _CLASS_RE.match(line)
            func_match = _FUNC_DECL_RE.match(line) if not class_match else None
            arrow_block_match = _CONST_ARROW_BLOCK_RE.match(line) if not (class_match or func_match) else None
            arrow_concise_match = (
                _CONST_ARROW_CONCISE_RE.match(line)
                if not (class_match or func_match or arrow_block_match) else None
            )

            if class_match:
                class_name = class_match.group(1)
                if "{" in line:
                    body_start = i
                    body_end = _find_block_end(masked_lines, body_start)
                    result.symbols.append(ExtractedSymbol(
                        name=class_name, qualified_name=class_name, type="class",
                        start_line=i + 1, end_line=body_end + 1,
                    ))
                    j = i + 1
                    inner_depth = line.count("{") - line.count("}")
                    while j <= body_end:
                        inner_line = masked_lines[j]
                        if inner_depth == 1:
                            meth = _METHOD_RE.match(inner_line)
                            meth_name = meth.group(1).lstrip("*") if meth else None
                            if meth and meth_name not in _JS_KEYWORDS_NOT_CALLS:
                                meth_end = _find_block_end(masked_lines, j)
                                qname = f"{class_name}.{meth_name}"
                                symbol, nested = _build_block_symbol(
                                    masked_lines, j, meth_end, meth_name, qname, "method",
                                    class_name, meth.group(2), file_is_test,
                                    enclosing_class=class_name,
                                )
                                result.symbols.append(symbol)
                                result.symbols.extend(nested)
                                j = meth_end + 1
                                continue  # inner_depth unchanged: skipped body is balanced
                        inner_depth += inner_line.count("{") - inner_line.count("}")
                        j += 1
                    i = body_end + 1
                    continue
                i += 1
                continue

            if func_match:
                func_name = func_match.group(1)
                if "{" in line:
                    body_end = _find_block_end(masked_lines, i)
                    symbol, nested = _build_block_symbol(
                        masked_lines, i, body_end, func_name, func_name, "function",
                        None, func_match.group(2), file_is_test,
                    )
                    result.symbols.append(symbol)
                    result.symbols.extend(nested)
                    i = body_end + 1
                    continue
                i += 1
                continue

            if arrow_block_match:
                func_name = arrow_block_match.group(1)
                body_end = _find_block_end(masked_lines, i)
                symbol, nested = _build_block_symbol(
                    masked_lines, i, body_end, func_name, func_name, "function",
                    None, arrow_block_match.group(2), file_is_test,
                )
                result.symbols.append(symbol)
                result.symbols.extend(nested)
                i = body_end + 1
                continue

            if arrow_concise_match:
                func_name = arrow_concise_match.group(1)
                param_blob = arrow_concise_match.group(2)
                expr_line = arrow_concise_match.group(3)
                top_calls, top_typed_calls = _extract_calls([expr_line])
                result.symbols.append(ExtractedSymbol(
                    name=func_name, qualified_name=func_name, type="function",
                    start_line=i + 1, end_line=i + 1,
                    calls=top_calls, typed_calls=top_typed_calls,
                    is_test=file_is_test or func_name.lower().startswith("test"),
                    local_bindings=_extract_local_bindings(param_blob, []),
                ))
                i += 1
                continue

        i += 1

    return result
