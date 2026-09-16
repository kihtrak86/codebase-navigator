"""
All repo/file/symbol endpoints. Every route requires a signed-in user
(via the get_current_user dependency) and every repo/symbol lookup is
scoped to that user's own data -- a symbol_id or repo_id belonging to
someone else's account returns 404, not the data, since IDs are plain
autoincrementing integers and guessable.
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from ..db import get_conn
from ..auth import get_current_user
from ..indexer.graph_builder import index_repository
from ..indexer.clone import CloneError
from ..indexer import analysis

router = APIRouter()


class IndexRequest(BaseModel):
    url: str


def _owned_repo_or_404(conn, repo_id: int, user_id: int):
    row = conn.execute("SELECT id FROM repositories WHERE id = ? AND user_id = ?", (repo_id, user_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Repository not found")


def _owned_symbol_or_404(conn, symbol_id: int, user_id: int):
    row = conn.execute(
        """SELECT s.id FROM symbols s JOIN repositories r ON r.id = s.repository_id
           WHERE s.id = ? AND r.user_id = ?""",
        (symbol_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Symbol not found")


@router.post("/repos/index")
def index_repo(req: IndexRequest, user=Depends(get_current_user)):
    try:
        result = index_repository(req.url, user_id=user["id"])
    except CloneError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {e}")
    return result


@router.get("/repos")
def list_repos(user=Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, url, commit_hash, created_at, file_count, symbol_count, dependency_count "
            "FROM repositories WHERE user_id = ? ORDER BY id DESC",
            (user["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.delete("/repos/{repo_id}")
def delete_repo(repo_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _owned_repo_or_404(conn, repo_id, user["id"])
        conn.execute("DELETE FROM repositories WHERE id=?", (repo_id,))
    return {"deleted": repo_id}


def _build_file_tree(rows: list[dict]) -> list[dict]:
    """Turns flat file paths into a nested folder/file tree, the way a file
    manager shows a project.

    Input rows are {id, path, language, symbol_count} with POSIX-style
    relative paths ("src/auth/login.py"). Output is a list of nodes:

        {type: "dir",  name, path, children: [...], file_count, symbol_count}
        {type: "file", name, path, id, language, symbol_count}

    Directories sort before files and each group sorts by name, so the
    listing is stable and reads like `ls`. Directory counts are rolled up
    from everything beneath them, so a collapsed folder still tells you
    how much is inside -- that rollup is why this is built here rather
    than left to the client to assemble.
    """
    root: dict = {"dirs": {}, "files": []}

    for row in rows:
        parts = row["path"].split("/")
        *folders, filename = parts
        node = root
        for folder in folders:
            node = node["dirs"].setdefault(folder, {"dirs": {}, "files": []})
        node["files"].append({**row, "name": filename})

    def emit(node: dict, prefix: str) -> tuple[list[dict], int, int]:
        children: list[dict] = []
        total_files = 0
        total_symbols = 0

        for name in sorted(node["dirs"]):
            dir_path = f"{prefix}{name}"
            sub_children, sub_files, sub_symbols = emit(node["dirs"][name], f"{dir_path}/")
            children.append({
                "type": "dir", "name": name, "path": dir_path,
                "children": sub_children,
                "file_count": sub_files, "symbol_count": sub_symbols,
            })
            total_files += sub_files
            total_symbols += sub_symbols

        for f in sorted(node["files"], key=lambda r: r["name"]):
            children.append({
                "type": "file", "name": f["name"], "path": f["path"],
                "id": f["id"], "language": f["language"],
                "symbol_count": f["symbol_count"],
            })
            total_files += 1
            total_symbols += f["symbol_count"]

        return children, total_files, total_symbols

    children, _, _ = emit(root, "")
    return children


@router.get("/repos/{repo_id}/tree")
def get_tree(repo_id: int, flat: bool = False, user=Depends(get_current_user)):
    """The repository's files. Nested folder/file tree by default; pass
    flat=true for the old flat list of file rows."""
    with get_conn() as conn:
        _owned_repo_or_404(conn, repo_id, user["id"])
        rows = conn.execute(
            """SELECT f.id, f.path, f.language, COUNT(s.id) AS symbol_count
               FROM files f LEFT JOIN symbols s ON s.file_id = f.id
               WHERE f.repository_id = ?
               GROUP BY f.id ORDER BY f.path""",
            (repo_id,),
        ).fetchall()
        rows = [dict(r) for r in rows]
    if flat:
        return rows
    return _build_file_tree(rows)


@router.get("/repos/{repo_id}/files/{file_id}/symbols")
def get_file_symbols(repo_id: int, file_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _owned_repo_or_404(conn, repo_id, user["id"])
        rows = conn.execute(
            """SELECT id, name, qualified_name, type, start_line, end_line, parent_symbol_id
               FROM symbols WHERE repository_id=? AND file_id=? ORDER BY start_line""",
            (repo_id, file_id),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/repos/{repo_id}/search")
def search_symbols(repo_id: int, q: str = Query(..., min_length=1), user=Depends(get_current_user)):
    """Name-based search over symbols (substring match). NL/semantic search is V3 -- not built yet."""
    with get_conn() as conn:
        _owned_repo_or_404(conn, repo_id, user["id"])
        rows = conn.execute(
            """SELECT s.id, s.qualified_name, s.type, f.path
               FROM symbols s JOIN files f ON f.id = s.file_id
               WHERE s.repository_id = ? AND s.qualified_name LIKE ?
               ORDER BY LENGTH(s.qualified_name) LIMIT 50""",
            (repo_id, f"%{q}%"),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/symbols/{symbol_id}")
def get_symbol(symbol_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _owned_symbol_or_404(conn, symbol_id, user["id"])
        row = conn.execute(
            """SELECT s.*, f.path FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id=?""",
            (symbol_id,),
        ).fetchone()
        symbol = dict(row)
    symbol["callers"] = analysis.get_callers(symbol_id)
    symbol["callees"] = analysis.get_callees(symbol_id)
    symbol["tests"] = analysis.get_tests_for_symbol(symbol_id)
    return symbol


@router.get("/symbols/{symbol_id}/impact")
def get_impact(symbol_id: int, max_depth: int = 5, user=Depends(get_current_user)):
    with get_conn() as conn:
        _owned_symbol_or_404(conn, symbol_id, user["id"])
    return analysis.impact_analysis(symbol_id, max_depth=max_depth)


@router.get("/symbols/{symbol_id}/graph")
def get_symbol_graph(symbol_id: int, depth: int = 2, user=Depends(get_current_user)):
    with get_conn() as conn:
        _owned_symbol_or_404(conn, symbol_id, user["id"])
    return analysis.build_symbol_graph(symbol_id, depth=depth)


@router.get("/symbols/{source_id}/path/{target_id}")
def get_path(source_id: int, target_id: int, user=Depends(get_current_user)):
    with get_conn() as conn:
        _owned_symbol_or_404(conn, source_id, user["id"])
        _owned_symbol_or_404(conn, target_id, user["id"])
    path = analysis.trace_path(source_id, target_id)
    if path is None:
        raise HTTPException(status_code=404, detail="No path found within search depth")
    return {"path": path}
