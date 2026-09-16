"""
Graph queries over the persisted dependency table: callers, callees,
impact (BFS downstream to a depth), and path tracing between two symbols.
"""
from collections import deque
from ..db import get_conn


def get_callers(symbol_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.qualified_name, s.type, f.path
               FROM dependencies d
               JOIN symbols s ON s.id = d.source_symbol_id
               JOIN files f ON f.id = s.file_id
               WHERE d.target_symbol_id = ?""",
            (symbol_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_callees(symbol_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.id, s.qualified_name, s.type, f.path
               FROM dependencies d
               JOIN symbols s ON s.id = d.target_symbol_id
               JOIN files f ON f.id = s.file_id
               WHERE d.source_symbol_id = ?""",
            (symbol_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_tests_for_symbol(symbol_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.id, s.qualified_name AS test_name, f.path
               FROM tests t
               JOIN symbols s ON s.id = t.symbol_id
               JOIN files f ON f.id = t.file_id
               WHERE t.covers_symbol_id = ?""",
            (symbol_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def impact_analysis(symbol_id: int, max_depth: int = 5) -> dict:
    """
    BFS over downstream-of-callers, i.e. everything that (transitively)
    calls `symbol_id` -- if I change this function, these are the things
    that could be affected because they depend on it.
    """
    with get_conn() as conn:
        visited = {symbol_id}
        frontier = deque([(symbol_id, 0)])
        affected_symbols: list[dict] = []
        affected_files = set()

        while frontier:
            current_id, depth = frontier.popleft()
            if depth >= max_depth:
                continue
            rows = conn.execute(
                """SELECT s.id, s.qualified_name, s.type, f.path, f.id as file_id
                   FROM dependencies d
                   JOIN symbols s ON s.id = d.source_symbol_id
                   JOIN files f ON f.id = s.file_id
                   WHERE d.target_symbol_id = ?""",
                (current_id,),
            ).fetchall()
            for row in rows:
                if row["id"] in visited:
                    continue
                visited.add(row["id"])
                affected_symbols.append({
                    "id": row["id"], "qualified_name": row["qualified_name"],
                    "type": row["type"], "path": row["path"], "depth": depth + 1,
                })
                affected_files.add(row["path"])
                frontier.append((row["id"], depth + 1))

        # tests covering the root symbol or any affected symbol
        affected_ids = [symbol_id] + [s["id"] for s in affected_symbols]
        placeholders = ",".join("?" * len(affected_ids))
        test_rows = conn.execute(
            f"""SELECT DISTINCT s.qualified_name AS test_name, f.path
                FROM tests t
                JOIN symbols s ON s.id = t.symbol_id
                JOIN files f ON f.id = t.file_id
                WHERE t.covers_symbol_id IN ({placeholders})""",
            affected_ids,
        ).fetchall()

        return {
            "root_symbol_id": symbol_id,
            "affected_symbols": affected_symbols,
            "affected_file_count": len(affected_files),
            "affected_files": sorted(affected_files),
            "affected_tests": [dict(r) for r in test_rows],
        }


def build_symbol_graph(symbol_id: int, depth: int = 2, max_nodes: int = 60) -> dict:
    """
    Builds a bounded node/edge subgraph centered on `symbol_id`, expanding
    outward through both callers and callees up to `depth` hops. This backs
    the graph-visualization tab -- rendering the *entire* repo graph isn't
    useful even for small repos and is actively unusable for a large one
    (Django's dependency table alone has 128k rows after the ambiguity-
    threshold fix; nobody can read a force-directed layout of that), so
    this always centers on one symbol and caps total node count rather than
    trying to render everything.
    """
    with get_conn() as conn:
        root_row = conn.execute(
            """SELECT s.id, s.qualified_name, s.type, f.path FROM symbols s
               JOIN files f ON f.id = s.file_id WHERE s.id = ?""",
            (symbol_id,),
        ).fetchone()
        if not root_row:
            return {"nodes": [], "edges": [], "truncated": False}

        nodes: dict[int, dict] = {
            symbol_id: {"id": symbol_id, "label": root_row["qualified_name"], "type": root_row["type"],
                        "path": root_row["path"], "root": True}
        }
        edges: list[dict] = []
        edge_seen: set[tuple[int, int]] = set()
        visited = {symbol_id}
        frontier = deque([(symbol_id, 0)])
        truncated = False

        while frontier:
            current_id, d = frontier.popleft()
            if d >= depth:
                continue
            if len(nodes) >= max_nodes:
                truncated = True
                break

            callee_rows = conn.execute(
                """SELECT s.id, s.qualified_name, s.type, f.path FROM dependencies dep
                   JOIN symbols s ON s.id = dep.target_symbol_id
                   JOIN files f ON f.id = s.file_id
                   WHERE dep.source_symbol_id = ?""",
                (current_id,),
            ).fetchall()
            caller_rows = conn.execute(
                """SELECT s.id, s.qualified_name, s.type, f.path FROM dependencies dep
                   JOIN symbols s ON s.id = dep.source_symbol_id
                   JOIN files f ON f.id = s.file_id
                   WHERE dep.target_symbol_id = ?""",
                (current_id,),
            ).fetchall()

            for row, direction in [(r, "out") for r in callee_rows] + [(r, "in") for r in caller_rows]:
                edge = (current_id, row["id"]) if direction == "out" else (row["id"], current_id)
                if edge not in edge_seen:
                    edge_seen.add(edge)
                    edges.append({"source": edge[0], "target": edge[1]})
                if row["id"] not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    nodes[row["id"]] = {"id": row["id"], "label": row["qualified_name"], "type": row["type"],
                                         "path": row["path"], "root": False}
                if row["id"] not in visited:
                    visited.add(row["id"])
                    frontier.append((row["id"], d + 1))

        return {"nodes": list(nodes.values()), "edges": edges, "truncated": truncated}


def trace_path(source_symbol_id: int, target_symbol_id: int, max_depth: int = 8) -> list[dict] | None:
    """BFS downstream (via calls) from source to target; returns the path as a list of symbol dicts, or None."""
    with get_conn() as conn:
        if source_symbol_id == target_symbol_id:
            row = conn.execute("SELECT id, qualified_name FROM symbols WHERE id=?", (source_symbol_id,)).fetchone()
            return [dict(row)] if row else None

        visited = {source_symbol_id}
        frontier = deque([[source_symbol_id]])

        while frontier:
            path = frontier.popleft()
            current_id = path[-1]
            if len(path) > max_depth:
                continue
            rows = conn.execute(
                """SELECT s.id, s.qualified_name FROM dependencies d
                   JOIN symbols s ON s.id = d.target_symbol_id
                   WHERE d.source_symbol_id = ?""",
                (current_id,),
            ).fetchall()
            for row in rows:
                if row["id"] in visited:
                    continue
                new_path = path + [row["id"]]
                if row["id"] == target_symbol_id:
                    ids = new_path
                    placeholders = ",".join("?" * len(ids))
                    order = {sid: i for i, sid in enumerate(ids)}
                    sym_rows = conn.execute(
                        f"SELECT id, qualified_name FROM symbols WHERE id IN ({placeholders})", ids
                    ).fetchall()
                    sym_rows = sorted(sym_rows, key=lambda r: order[r["id"]])
                    return [dict(r) for r in sym_rows]
                visited.add(row["id"])
                frontier.append(new_path)
        return None
