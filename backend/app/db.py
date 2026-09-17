"""
SQLite storage layer for Codebase Navigator.

Mirrors the data model from the project draft (Repository / File / Symbol /
Dependency / Test / CodeEmbedding) but uses SQLite instead of
Postgres+pgvector so the whole project runs with zero external services.
Swapping in Postgres later just means changing this module's connection
string and, if needed, the embedding column type.
"""
import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("CODENAV_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "codenav.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- User-defined folders for organizing indexed repositories, the way a file
-- manager groups files. A repo with folder_id NULL just sits "unfiled" in
-- the top-level list -- folders are an optional grouping layer, not a
-- required home, so nothing else about a repo's behavior depends on one.
CREATE TABLE IF NOT EXISTS repo_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    commit_hash TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    file_count INTEGER DEFAULT 0,
    symbol_count INTEGER DEFAULT 0,
    dependency_count INTEGER DEFAULT 0,
    -- ON DELETE SET NULL: deleting a folder un-files its repos rather than
    -- deleting them -- a folder is just a label, not a container the repos
    -- live inside.
    folder_id INTEGER REFERENCES repo_folders(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    language TEXT,
    UNIQUE(repository_id, path)
);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    type TEXT NOT NULL,           -- 'class' | 'function' | 'method'
    start_line INTEGER,
    end_line INTEGER,
    parent_symbol_id INTEGER REFERENCES symbols(id)
);

CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    source_symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    target_symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL   -- 'calls' | 'imports'
);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    symbol_id INTEGER REFERENCES symbols(id),   -- the test symbol itself
    covers_symbol_id INTEGER REFERENCES symbols(id),  -- best-effort: what it likely tests
    test_type TEXT DEFAULT 'unit'
);

CREATE TABLE IF NOT EXISTS code_embeddings (
    symbol_id INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    embedding TEXT   -- placeholder for V3 (JSON-encoded vector or NULL until pgvector/real embeddings land)
);

-- Password reset: a random token is hashed (sha256) before storage, same
-- "never store the secret itself" principle as password hashing -- a DB
-- leak doesn't hand out usable reset tokens. One row per outstanding
-- request; used_at is set on redemption so a token can't be replayed.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_repo ON symbols(repository_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_deps_source ON dependencies(source_symbol_id);
CREATE INDEX IF NOT EXISTS idx_deps_target ON dependencies(target_symbol_id);
CREATE INDEX IF NOT EXISTS idx_files_repo ON files(repository_id);
CREATE INDEX IF NOT EXISTS idx_repos_user ON repositories(user_id);
CREATE INDEX IF NOT EXISTS idx_repos_folder ON repositories(folder_id);
CREATE INDEX IF NOT EXISTS idx_folders_user ON repo_folders(user_id);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_hash ON password_reset_tokens(token_hash);
"""


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migration for DBs created before folders existed: CREATE TABLE IF
                                                                   
                                                                         
                             
        try:
            conn.execute("ALTER TABLE repositories ADD COLUMN folder_id INTEGER REFERENCES repo_folders(id) ON DELETE SET NULL")
        except sqlite3.OperationalError:
            pass


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
