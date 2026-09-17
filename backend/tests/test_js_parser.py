"""
Tests for js_parser.py. Several of these encode specific regressions found
while testing against a real repo (see README's "Call resolution: what
changed, and why" / "Round three" sections) -- if one of these starts
failing, it means one of those bugs came back.
"""
from app.indexer.js_parser import parse_js_file


def test_function_declaration_does_not_self_call():
    """Regression: `function Pagination(...) {` was matching its own name
    in the signature as a call to itself."""
    src = "export default function Pagination({ page, onPageChange }) {\n  onPageChange(page);\n}\n"
    result = parse_js_file(src, "Pagination.jsx")
    fn = result.symbols[0]
    assert fn.name == "Pagination"
    assert "Pagination" not in fn.calls
    assert "onPageChange" in fn.calls


def test_class_and_methods_extracted_with_correct_parent():
    src = """
export class UserService {
  constructor(repo) { this.repo = repo; }
  async updateUser(id, data) {
    this.validate(data);
    return this.repo.save(id, data);
  }
  validate(data) { if (!data.email) throw new Error("bad"); }
}
"""
    result = parse_js_file(src, "UserService.js")
    names = {s.qualified_name: s for s in result.symbols}
    assert set(names) == {"UserService", "UserService.constructor", "UserService.updateUser", "UserService.validate"}
    assert names["UserService.updateUser"].parent_qualified_name == "UserService"
    # this.validate(data): receiver is `this`, so it's typed to the
                                                                      
    assert ("UserService", "validate") in names["UserService.updateUser"].typed_calls
    assert "validate" not in names["UserService.updateUser"].calls
                                                                        
                                                           
    assert "save" in names["UserService.updateUser"].calls


def test_concise_arrow_function_is_extracted():
    """Regression: `export const foo = (x) => expr;` (no braces) wasn't
    extracted as a symbol at all."""
    src = 'export const signupRequest = (payload) => api.post("/auth/signup", payload).then((res) => res.data);\n'
    result = parse_js_file(src, "api.js")
    assert len(result.symbols) == 1
    fn = result.symbols[0]
    assert fn.name == "signupRequest"
    assert fn.start_line == fn.end_line
    assert "post" in fn.calls


def test_hook_wrapped_arrow_function_is_extracted():
    """Regression: `const login = useCallback(async (...) => {...}, [])` --
    the arrow wrapped in a hook call -- wasn't matched by the original
    bare-arrow-only regex."""
    src = """
export function AuthProvider() {
  const login = useCallback(async (email, password) => {
    const res = await loginRequest(email, password);
    return res;
  }, []);
}
"""
    result = parse_js_file(src, "AuthContext.jsx")
    names = {s.qualified_name for s in result.symbols}
    assert "AuthProvider.login" in names
    login = next(s for s in result.symbols if s.qualified_name == "AuthProvider.login")
    assert "loginRequest" in login.calls


def test_local_destructured_binding_is_not_a_call_target():
    """Regression: `const { signup } = useAuth(); ... signup(...)` was
    resolving to an unrelated same-named function elsewhere in the repo,
    because the destructured local variable wasn't tracked as shadowing."""
    src = """
function Signup() {
  const { signup } = useAuth();
  const handleSubmit = (e) => {
    signup(form);
  };
}
"""
    result = parse_js_file(src, "Signup.jsx")
    names = {s.qualified_name: s for s in result.symbols}
    assert "signup" in names["Signup"].local_bindings
                                                                                             
    assert "signup" in names["Signup.handleSubmit"].local_bindings


def test_nested_functions_extracted_at_arbitrary_depth():
    src = """
export function Outer() {
  const middle = () => {
    const inner = () => {
      doSomething();
    };
    inner();
  };
  middle();
}
"""
    result = parse_js_file(src, "test.jsx")
    names = {s.qualified_name: s for s in result.symbols}
    assert set(names) == {"Outer", "Outer.middle", "Outer.middle.inner"}
    assert names["Outer.middle.inner"].calls == ["doSomething"]
                                                                                           
    assert "doSomething" not in names["Outer"].calls
    assert "doSomething" not in names["Outer.middle"].calls
    assert "inner" not in names["Outer"].calls                                             


def test_sibling_nested_functions_do_not_leak_scope():
    """Regression (round three): a name declared inside one nested function
    was leaking into its *sibling*, not just its own descendants -- a
    false-negative bug where a genuine call would be wrongly suppressed."""
    src = """
export function Outer() {
  const helperA = () => {
    const secret = getSecret();
    return secret;
  };
  const helperB = () => {
    return secret();
  };
}
"""
    result = parse_js_file(src, "test.jsx")
    names = {s.qualified_name: s for s in result.symbols}
    assert "secret" in names["Outer.helperA"].local_bindings
    assert "secret" not in names["Outer.helperB"].local_bindings
    assert "secret" not in names["Outer"].local_bindings


def test_structured_imports_named_default_and_commonjs():
    src = """
import React from 'react';
import { useCallback, useState as useSt } from 'react';
import * as Utils from './utils';
const { foo, bar: baz } = require('./helpers');
const Config = require('./config');
"""
    result = parse_js_file(src, "a.jsx")
    by_local = {b.local_name: b for b in result.imports}
    assert by_local["React"].module == "react" and by_local["React"].orig_name is None
    assert by_local["useCallback"].orig_name == "useCallback"
    assert by_local["useSt"].orig_name == "useState"
    assert by_local["Utils"].orig_name == "*"
    assert by_local["foo"].module == "./helpers" and by_local["foo"].orig_name == "foo"
    assert by_local["baz"].orig_name == "bar"
    assert by_local["Config"].module == "./config"


def test_is_test_file_detection():
    result = parse_js_file("function helper() {}\n", "src/utils.test.js")
    assert result.symbols[0].is_test is True

    result2 = parse_js_file("function testThing() {}\n", "src/utils.js")
    assert result2.symbols[0].is_test is True

    result3 = parse_js_file("function helper() {}\n", "src/utils.js")
    assert result3.symbols[0].is_test is False
