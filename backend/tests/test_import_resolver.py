"""Tests for import_resolver.py -- resolving import specifiers to actual
files in the repo."""
from app.indexer.import_resolver import resolve_import_target, build_import_maps
from app.indexer.common import ExtractedFile, ImportBinding


def test_js_relative_import_resolves_to_existing_file():
    known = {"client/src/pages/Signup.jsx", "client/src/context/AuthContext.jsx"}
    target = resolve_import_target("client/src/pages/Signup.jsx", "../context/AuthContext", known, "javascript")
    assert target == "client/src/context/AuthContext.jsx"


def test_js_relative_import_resolves_index_file():
    known = {"client/src/pages/Home.jsx", "client/src/components/index.js"}
    target = resolve_import_target("client/src/pages/Home.jsx", "../components", known, "javascript")
    assert target == "client/src/components/index.js"


def test_js_bare_specifier_is_external():
    known = {"client/src/pages/Home.jsx"}
    assert resolve_import_target("client/src/pages/Home.jsx", "react", known, "javascript") is None


def test_python_relative_import_one_dot_is_same_directory():
    known = {"app/services.py", "app/models.py"}
    target = resolve_import_target("app/services.py", ".models", known, "python")
    assert target == "app/models.py"


def test_python_relative_import_two_dots_goes_up_a_level():
    known = {"app/services.py", "utils.py"}
    target = resolve_import_target("app/services.py", "..utils", known, "python")
    assert target == "utils.py"


def test_python_absolute_dotted_import_resolves_to_package_init():
    known = {"app/services.py", "app/utils/__init__.py"}
    target = resolve_import_target("app/services.py", "app.utils", known, "python")
    assert target == "app/utils/__init__.py"


def test_python_unresolvable_import_is_external():
    known = {"app/services.py"}
    assert resolve_import_target("app/services.py", "requests", known, "python") is None


def test_build_import_maps_covers_every_file():
    files = [
        ExtractedFile(relpath="a.py", language="python",
                      imports=[ImportBinding(local_name="helper", module=".b", orig_name="helper")]),
        ExtractedFile(relpath="b.py", language="python", imports=[]),
    ]
    maps = build_import_maps(files)
    assert maps["a.py"]["helper"] == ("file", "b.py", "helper")
