"""
Shared output shape for all language parsers. Every parser (python_parser.py,
js_parser.py, and any future one) produces these same dataclasses so the
rest of the pipeline (graph_builder.py) never needs to know which language
a file was written in.
"""
from dataclasses import dataclass, field


@dataclass
class ImportBinding:
    """One name bound into a file's scope by an import/require statement."""
    local_name: str          # the identifier as used in this file's code
    module: str               # raw specifier as written: './utils', 'os.path', 'react'
    orig_name: str | None = None  # name in the source module, e.g. `import { orig_name as local_name }`;
                                   # None for a default/whole-module binding


@dataclass
class ExtractedSymbol:
    name: str                # simple name, e.g. "update_user"
    qualified_name: str      # e.g. "UserService.update_user" or module-level "checkout"
    type: str                # 'class' | 'function' | 'method'
    start_line: int
    end_line: int
    parent_qualified_name: str | None = None
    calls: list[str] = field(default_factory=list)   # raw call names referenced in the body
    is_test: bool = False
    local_bindings: set[str] = field(default_factory=set)
    # Names bound by a local variable/destructuring declaration inside this
    # symbol's own body (e.g. `const { signup } = useAuth();`). A call using
    # one of these names refers to that local value, not to some same-named
    # symbol elsewhere in the repo -- so it must never fall back to
    # repo-wide name matching. See js_parser.py for how this is populated.
    typed_calls: list[tuple[str, str]] = field(default_factory=list)
    # Attribute calls (`receiver.method()`) where the receiver's *type* was
    # inferable, as (receiver_type, method_name) -- e.g. `self.foo()` inside
    # `class Bar` becomes ("Bar", "foo"); `x = PaymentGateway(); x.charge()`
    # becomes ("PaymentGateway", "charge"). These are resolved by
    # graph_builder against the *specific* class's method
    # (`PaymentGateway.charge`) instead of every same-named method
    # repo-wide, which is what let Django's 7 distinct `filter()` methods
    # all get linked from a single ambiguous call before this existed.
    # Best-effort and deliberately narrow (see each parser's own notes on
    # what it does and doesn't infer) -- a call that ends up here is never
    # also present in `calls`, so it's resolved via receiver type alone or
    # not at all, never both ways.


@dataclass
class ExtractedFile:
    relpath: str
    language: str
    imports: list[ImportBinding] = field(default_factory=list)
    symbols: list[ExtractedSymbol] = field(default_factory=list)
