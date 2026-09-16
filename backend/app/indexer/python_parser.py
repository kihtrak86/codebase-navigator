"""
Parses Python source into symbols (classes/functions/methods), structured
import bindings, and raw call references. Cross-symbol call resolution
happens later in graph_builder.py once every file's symbols are known,
since a call target may live in a different file than the caller.

This stands in for the Tree-sitter stage in the roadmap. Swapping to
Tree-sitter later mainly means replacing this module's extraction logic
while keeping the same output shape (ExtractedFile), so the rest of the
pipeline (graph_builder, DB, API) is largely language-agnostic already.
"""
import ast
import os
from .common import ExtractedSymbol, ExtractedFile, ImportBinding


class _CallCollector(ast.NodeVisitor):
    """Collects the simple/attribute names of every call inside a function body,
    without descending into nested function/class defs (those get their own visit).

    Also does light, best-effort receiver-type inference for attribute
    calls (`receiver.method()`): if the receiver is `self`/`cls` and this
    function is a method, the receiver's type is the enclosing class. If
    the receiver is a plain local variable most recently assigned from
    `ClassName(...)` (a known in-file class, or anything that looks like a
    constructor call -- PascalCase, by convention), the receiver's type is
    that class name. When a type is known, the call is recorded in
    `typed_calls` as (receiver_type, method_name) instead of `calls`, so
    graph_builder can resolve it against that *specific* class's method
    rather than every same-named method repo-wide.

    Deliberately narrow: only a direct `x = ClassName(...)` assignment is
    tracked (not `x = get_thing()`, not attribute chains like
    `self.repo.save()` where the receiver is itself an Attribute, not a
    Name) -- guessing further would trade the false-positive this exists
    to prevent for a different one. A linear, single pass over the body in
    source order, with no control-flow awareness: a reassignment later in
    the function overwrites the tracked type for everything after it, and
    an `if`/`else` that assigns different types on each branch will just
    reflect whichever branch is textually last -- acceptable for a
    best-effort hint, not treated as ground truth.
    """

    def __init__(self, enclosing_class: str | None = None):
        self.calls: list[str] = []
        self.typed_calls: list[tuple[str, str]] = []
        self.enclosing_class = enclosing_class
        self._var_types: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_name = node.targets[0].id
            cls_name = _inferred_constructor_class(node.value)
            if cls_name:
                self._var_types[target_name] = cls_name
            else:
                self._var_types.pop(target_name, None)  # reassigned to something unknown -- drop the stale hint
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver_type = self._infer_receiver_type(func.value)
            if receiver_type:
                self.typed_calls.append((receiver_type, func.attr))
            else:
                self.calls.append(func.attr)
        elif isinstance(func, ast.Name):
            self.calls.append(func.id)
        self.generic_visit(node)

    def _infer_receiver_type(self, receiver: ast.expr) -> str | None:
        if isinstance(receiver, ast.Name):
            if receiver.id in ("self", "cls") and self.enclosing_class:
                return self.enclosing_class
            return self._var_types.get(receiver.id)
        return None

    def visit_FunctionDef(self, node):
        pass  # don't descend into nested defs; they're separate symbols

    def visit_AsyncFunctionDef(self, node):
        pass

    def visit_ClassDef(self, node):
        pass


def _inferred_constructor_class(value: ast.expr) -> str | None:
    """`ClassName(...)` -> "ClassName", best-effort: the name just needs to
    look like a class (PascalCase, by convention -- e.g. `PaymentGateway`,
    not `get_gateway`). Doesn't check the name actually resolves to a real
    class; graph_builder's qualified-name lookup simply won't find a match
    if it doesn't, which is the same "unresolved beats a wrong guess"
    stance used everywhere else in this pipeline."""
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        name = value.func.id
        if name[:1].isupper():
            return name
    return None


class _LocalBindingCollector(ast.NodeVisitor):
    """Collects names bound by plain assignment/for/with inside a function
    body (shallow -- doesn't descend into nested defs). These shadow
    repo-wide symbols of the same name: `user = get_current_user()` then
    calling `user.save()` should never make `save` resolve to some
    unrelated top-level `save` function elsewhere in the repo."""

    def __init__(self):
        self.names: set[str] = set()

    def visit_FunctionDef(self, node):
        pass

    def visit_AsyncFunctionDef(self, node):
        pass

    def visit_ClassDef(self, node):
        pass

    def visit_Assign(self, node):
        for t in node.targets:
            self._add_target(t)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._add_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node):
        self._add_target(node.target)
        self.generic_visit(node)

    def visit_With(self, node):
        for item in node.items:
            if item.optional_vars:
                self._add_target(item.optional_vars)
        self.generic_visit(node)

    def _add_target(self, t):
        if isinstance(t, ast.Name):
            self.names.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for elt in t.elts:
                self._add_target(elt)


def _is_test_file(relpath: str) -> bool:
    base = os.path.basename(relpath).lower()
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in relpath.replace("\\", "/")


def parse_python_file(source: str, relpath: str) -> ExtractedFile:
    result = ExtractedFile(relpath=relpath, language="python")
    try:
        tree = ast.parse(source, filename=relpath)
    except SyntaxError:
        return result  # skip unparsable files rather than failing the whole index

    file_is_test = _is_test_file(relpath)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                result.imports.append(ImportBinding(local_name=local_name, module=alias.name, orig_name=None))
        elif isinstance(node, ast.ImportFrom):
            module_str = ("." * node.level) + (node.module or "")
            for alias in node.names:
                if alias.name == "*":
                    continue  # star imports can't be resolved to a specific name
                local_name = alias.asname or alias.name
                result.imports.append(ImportBinding(local_name=local_name, module=module_str, orig_name=alias.name))

    def _collect_calls(body_node, enclosing_class: str | None = None) -> tuple[list[str], list[tuple[str, str]]]:
        collector = _CallCollector(enclosing_class=enclosing_class)
        for stmt in body_node.body:
            collector.visit(stmt)
        return collector.calls, collector.typed_calls

    def _collect_local_bindings(body_node) -> set[str]:
        collector = _LocalBindingCollector()
        for stmt in body_node.body:
            collector.visit(stmt)
        args = body_node.args
        for a in (args.posonlyargs + args.args + args.kwonlyargs
                  + ([args.vararg] if args.vararg else [])
                  + ([args.kwarg] if args.kwarg else [])):
            collector.names.add(a.arg)
        return collector.names

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_symbol = ExtractedSymbol(
                name=node.name,
                qualified_name=node.name,
                type="class",
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
            )
            result.symbols.append(class_symbol)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qname = f"{node.name}.{sub.name}"
                    is_test = file_is_test or sub.name.startswith("test_")
                    sub_calls, sub_typed_calls = _collect_calls(sub, enclosing_class=node.name)
                    result.symbols.append(ExtractedSymbol(
                        name=sub.name,
                        qualified_name=qname,
                        type="method",
                        start_line=sub.lineno,
                        end_line=getattr(sub, "end_lineno", sub.lineno),
                        parent_qualified_name=node.name,
                        calls=sub_calls,
                        typed_calls=sub_typed_calls,
                        is_test=is_test,
                        local_bindings=_collect_local_bindings(sub),
                    ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_test = file_is_test or node.name.startswith("test_")
            fn_calls, fn_typed_calls = _collect_calls(node)
            result.symbols.append(ExtractedSymbol(
                name=node.name,
                qualified_name=node.name,
                type="function",
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                calls=fn_calls,
                typed_calls=fn_typed_calls,
                is_test=is_test,
                local_bindings=_collect_local_bindings(node),
            ))

    return result
