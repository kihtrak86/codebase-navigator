"""
Resolves an ImportBinding's module specifier to an actual file in the repo,
so graph_builder.py can prefer "this call was imported from that specific
file" over guessing by name across the whole repository.

A specifier that can't be resolved to a repo file is either a relative path
that genuinely doesn't exist (typo, or a file type we don't parse) or a bare
package name (react, axios, os.path, mongoose...) -- either way, external.
Calls bound to an external import are deliberately left unresolved rather
than falling back to name matching: we *know* they don't come from this
repo, so guessing would be strictly worse than saying nothing.
"""
import os


def resolve_import_target(current_relpath: str, module_specifier: str, known_relpaths: set[str], language: str) -> str | None:
    if language == "python":
        return _resolve_python_module(current_relpath, module_specifier, known_relpaths)
    if module_specifier.startswith("."):
        return _resolve_js_relative(current_relpath, module_specifier, known_relpaths)
    return None  # bare specifier ('react', '@nestjs/common', ...) -- external package


def _resolve_js_relative(current_relpath: str, spec: str, known: set[str]) -> str | None:
    base_dir = os.path.dirname(current_relpath)
    combined = os.path.normpath(os.path.join(base_dir, spec)).replace(os.sep, "/")
    candidates = [combined]
    for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        candidates.append(combined + ext)
    for ext in (".js", ".jsx", ".ts", ".tsx"):
        candidates.append(combined + "/index" + ext)
    for c in candidates:
        if c in known:
            return c
    return None


def _resolve_python_module(current_relpath: str, spec: str, known: set[str]) -> str | None:
    if spec.startswith("."):
        level = 0
        while level < len(spec) and spec[level] == ".":
            level += 1
        remainder = spec[level:]                                                 
        base_dir = os.path.dirname(current_relpath)
        for _ in range(level - 1):
            base_dir = os.path.dirname(base_dir)
        combined = os.path.normpath(os.path.join(base_dir, remainder.replace(".", "/"))).replace(os.sep, "/") \
            if remainder else base_dir
    else:
        combined = spec.replace(".", "/")
    for candidate in (combined + ".py", (combined + "/__init__.py") if combined else "__init__.py"):
        if candidate in known:
            return candidate
    return None


def build_import_maps(extracted_files: list) -> dict[str, dict[str, tuple]]:
    """Returns {file_relpath: {local_name: resolution}} where resolution is
    either ('file', target_relpath, orig_name_or_None) or ('external', module)."""
    known = {ef.relpath for ef in extracted_files}
    result: dict[str, dict[str, tuple]] = {}
    for ef in extracted_files:
        file_map: dict[str, tuple] = {}
        for binding in ef.imports:
            target = resolve_import_target(ef.relpath, binding.module, known, ef.language)
            if target:
                file_map[binding.local_name] = ("file", target, binding.orig_name)
            else:
                file_map[binding.local_name] = ("external", binding.module)
        result[ef.relpath] = file_map
    return result
