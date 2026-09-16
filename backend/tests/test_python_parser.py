"""Tests for python_parser.py -- import extraction, call extraction, and
local-binding tracking."""
from app.indexer.python_parser import parse_python_file


def test_extracts_functions_and_classes():
    src = """
class UserService:
    def update_user(self, uid):
        return self.repo.save(uid)

def standalone():
    return 1
"""
    result = parse_python_file(src, "app/services.py")
    names = {s.qualified_name for s in result.symbols}
    assert names == {"UserService", "UserService.update_user", "standalone"}
    method = next(s for s in result.symbols if s.qualified_name == "UserService.update_user")
    assert method.type == "method"
    assert method.parent_qualified_name == "UserService"
    assert "save" in method.calls


def test_import_bindings_capture_local_and_original_names():
    src = """
from .models import User
from ..utils import helper as h
import os
"""
    result = parse_python_file(src, "app/services.py")
    by_local = {b.local_name: b for b in result.imports}
    assert by_local["User"].module == ".models"
    assert by_local["User"].orig_name == "User"
    assert by_local["h"].module == "..utils"
    assert by_local["h"].orig_name == "helper"
    assert by_local["os"].module == "os"
    assert by_local["os"].orig_name is None


def test_star_import_is_skipped_not_crashed_on():
    src = "from os.path import *\n"
    result = parse_python_file(src, "a.py")
    assert result.imports == []  # can't resolve a specific name, so nothing is recorded


def test_local_bindings_include_params_and_assignments():
    src = """
def handler(request, uid):
    user = get_user(uid)
    return user.save()
"""
    result = parse_python_file(src, "a.py")
    fn = result.symbols[0]
    assert {"request", "uid", "user"} <= fn.local_bindings
    assert "save" in fn.calls  # attribute call is still recorded; shadowing is graph_builder's job


def test_is_test_file_detection():
    result = parse_python_file("def test_foo():\n    pass\n", "tests/test_app.py")
    assert result.symbols[0].is_test is True

    result2 = parse_python_file("def test_foo():\n    pass\n", "app.py")
    assert result2.symbols[0].is_test is True  # name-based detection still applies

    result3 = parse_python_file("def helper():\n    pass\n", "app.py")
    assert result3.symbols[0].is_test is False


def test_unparsable_file_returns_empty_result_not_exception():
    result = parse_python_file("def broken(:\n", "broken.py")
    assert result.symbols == []
    assert result.imports == []
