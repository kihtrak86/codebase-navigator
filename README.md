# Codebase Navigator

![tests](https://img.shields.io/badge/tests-69%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.12%2B-blue)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

**Turn an unfamiliar codebase into a structural graph you can actually query.**
Index a GitHub repository and ask "what calls this?", "what breaks if I
change this?", and "which tests cover this?" — answered from a real
dependency graph built by parsing the code, not a text search index.

<!--
  Add screenshots/GIF here before publishing — e.g.:
  ![Homepage](docs/screenshots/homepage.png)
  ![Symbol detail + graph view](docs/screenshots/graph.png)
  Capture at 1440px: homepage, the symbol detail view with the tab strip,
  and the D3 graph visualization tab.
-->

## What it does

Paste a GitHub URL, and Codebase Navigator clones it, parses every Python
and JavaScript/JSX/TypeScript/TSX file into files → symbols → imports →
calls, and builds a real call graph from it. From there you can:

- **Browse** the repo as a file tree with per-symbol detail (type, location,
  callers, callees, tests)
- **Ask "what breaks if I change this?"** — BFS impact analysis over the
  actual dependency graph, not a guess
- **See it, not just read it** — a symbol's neighborhood rendered as an
  interactive force-directed graph
- **Trace a path** between any two symbols — how does a request reach this
  function, five calls deep?
- **Keep your work** — every indexed repo is tied to your account and is
  there next time you sign in

## Why this is more interesting than it sounds

The hard part of a tool like this isn't cloning a repo — it's resolving
`someFunction()` to the *right* `someFunction`, correctly, across files,
imports, closures, and thousands of same-named methods. This project's
call resolution went through nine documented rounds of "index a real repo,
find a real wrong answer, fix it, add a regression test for it" — including
a stress test against Django that found **1,040,997 spurious dependency
edges** from a single overlooked case, and a fix for the exact ambiguity
that makes `queryset.filter()` and `logger.filter()` indistinguishable to a
naive resolver. The full engineering narrative — every bug, every wrong
turn, every fix, with the real numbers — is in
**[docs/ENGINEERING.md](docs/ENGINEERING.md)**. It's long on purpose: it's
the most honest record of how this was actually built.

## Quickstart

```bash
git clone <this-repo>
cd codebase-navigator/backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`, sign up (any email + password ≥8 characters —
there's no email verification yet, see Limitations), and paste a GitHub URL.

Set `CODENAV_SECRET_KEY` to a random string if you want sessions to survive
a server restart:

```bash
export CODENAV_SECRET_KEY="something-long-and-random"
```

**If you have a `codenav.db` from an older version**, delete it — there's
no migration system yet, and the schema has changed several times.

## Tech stack

- **Backend:** FastAPI + SQLite (no ORM — hand-written SQL, on purpose, to
  keep the schema and every query visible)
- **Parsing:** Python's own `ast` module; a hand-written regex/brace-counting
  parser for JS/JSX/TS/TSX (no Tree-sitter dependency)
- **Auth:** stdlib PBKDF2 password hashing, Starlette signed-cookie sessions
  — no external auth service
- **Frontend:** vanilla JS, no framework, no build step; D3.js (via CDN) for
  the graph visualization
- **Tests:** pytest, 69 tests — parsers, import resolution, the full
  indexing pipeline end-to-end against local git fixtures, and the HTTP API

## Architecture

```
Browser (vanilla JS, D3)
        │  fetch() + session cookie
        ▼
FastAPI app  ──auth_routes.py──▶  accounts, sessions, password reset
    │
    └──routes.py──▶  repos / files / symbols / graph  (all ownership-checked)
                              │
                              ▼
                    indexer/graph_builder.py
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
       python_parser.py  js_parser.py   import_resolver.py
              │               │                │
              └───────────────┴────────────────┘
                              ▼
                          SQLite
          (users, repositories, files, symbols, dependencies, tests)
```

## Project layout

```
backend/
  app/
    main.py, db.py, auth.py       app setup, schema, accounts/sessions
    indexer/                      clone → parse → resolve → persist
    api/                          routes.py (data), auth_routes.py (accounts)
  tests/                          69 pytest tests, see docs/ENGINEERING.md
frontend/
  index.html, auth.html,          homepage / auth / app — separate pages,
  app.html, account.html          not a single-page toggle
  css/, js/, assets/              per-page styles/scripts, generated SVG art
docs/
  ENGINEERING.md                  the full build narrative
```

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

69 tests. Every real bug found while building this has a named regression
test guarding it — see `docs/ENGINEERING.md` for what each one caught and
why it existed.

## Known limitations

Stated plainly rather than discovered the hard way:

- **No email verification** on signup, and the session-signing secret is
  regenerated on every restart unless `CODENAV_SECRET_KEY` is set — fine
  for local use, not for anything public-facing yet.
- **Call resolution is best-effort, not a type system.** It handles
  `self.foo()`, `this.foo()`, and `x = ClassName(); x.method()` correctly,
  but a chained attribute (`self.repo.save()`) or a receiver typed from a
  function's return value stays unresolved rather than guessed.
- **No natural-language search yet.** The retrieval half (symbols,
  dependency graph, tests) is built; there's no LLM layer over it.
- **No git history, PR analysis, or Docker/deployment config yet.**

Full detail on all of these, plus what was tried and what's next, is in
[docs/ENGINEERING.md](docs/ENGINEERING.md).

## License

MIT — see [LICENSE](LICENSE).
