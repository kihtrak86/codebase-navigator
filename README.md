# Codebase Navigator

Codebase Navigator is a small static-analysis tool for understanding an unfamiliar repository.

Give it a GitHub repository URL and it builds a searchable dependency graph of the code. You can browse files and symbols, inspect callers and callees, trace a path between symbols, and see which parts of the code are affected by a change.

It currently supports **Python, JavaScript, JSX, TypeScript, and TSX**.

## What it does

- Indexes a Git repository and stores the result in SQLite
- Parses Python with Python's built-in `ast` module
- Parses JavaScript/JSX/TypeScript/TSX with a lightweight custom parser
- Resolves imports and many function/method calls across files
- Shows files, functions, classes, and methods in a file tree
- Shows callers, callees, and related tests for a symbol
- Runs breadth-first impact analysis
- Traces call paths between symbols
- Displays a symbol's local dependency graph with D3
- Keeps indexed repositories associated with a user account

The project deliberately does **not** use an LLM. The graph is produced from the source code itself, so the same input produces the same analysis.

## How it works

The indexing pipeline is:

```text
Git repository
      │
      ▼
   Clone
      │
      ▼
    Parse
  ┌───┴───────────────┐
  │                   │
Python AST       JS/TS parser
  │                   │
  └─────────┬─────────┘
            ▼
    Import resolution
            │
            ▼
      Dependency graph
            │
            ▼
          SQLite
            │
            ▼
       API + frontend
```

The important part is the resolution step. A call is not simply connected to every symbol with the same name. The resolver first considers local bindings, then imports and known receiver types, and only uses repo-wide name matching when the call is otherwise ambiguous. This keeps the graph useful on real codebases where names are reused.

More detail about the implementation and the bugs found while testing it is in [`docs/ENGINEERING.md`](docs/ENGINEERING.md).

## Tech stack

**Backend**
- Python 3.12+
- FastAPI
- SQLite
- Hand-written SQL
- pytest

**Parsing**
- Python `ast`
- Custom parser for JavaScript / JSX / TypeScript / TSX

**Authentication**
- PBKDF2 password hashing
- Starlette signed-cookie sessions

**Frontend**
- HTML/CSS
- Vanilla JavaScript
- D3.js for graph visualization

There is no frontend build step.

## Running locally

### Requirements

- Python 3.12 or newer
- Git

### 1. Clone the project

```bash
git clone <repository-url>
cd codebase-navigator
```

### 2. Install dependencies

```bash
cd backend
python -m venv .venv
```

Activate the virtual environment:

**Windows**

```powershell
.venv\Scripts\activate
```

**macOS / Linux**

```bash
source .venv/bin/activate
```

Then install the dependencies:

```bash
pip install -r requirements.txt
```

### 3. Start the server

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

The frontend is served by the FastAPI application, so a separate frontend server is not required.

Create an account and paste a GitHub repository URL to start indexing.

### Session secret

For local use, the application can generate a session secret automatically. If you want sessions to remain valid after restarting the server, set `CODENAV_SECRET_KEY` to a long random value before starting the application.

PowerShell:

```powershell
$env:CODENAV_SECRET_KEY="your-random-secret"
```

macOS / Linux:

```bash
export CODENAV_SECRET_KEY="your-random-secret"
```

## Running tests

Install the development dependencies:

```bash
cd backend
pip install -r requirements-dev.txt
```

Run:

```bash
pytest
```

The test suite covers:

- Python parsing
- JavaScript/TypeScript parsing
- Import resolution
- Call and method resolution
- Nested functions and scope handling
- Dependency graph construction
- Repository indexing
- API endpoints
- Authentication and account operations

## Project structure

```text
codebase-navigator/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── auth_routes.py
│   │   │   └── routes.py
│   │   ├── indexer/
│   │   │   ├── analysis.py
│   │   │   ├── clone.py
│   │   │   ├── graph_builder.py
│   │   │   ├── import_resolver.py
│   │   │   ├── js_parser.py
│   │   │   └── python_parser.py
│   │   ├── auth.py
│   │   ├── db.py
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── docs/
│   └── ENGINEERING.md
├── frontend/
│   ├── index.html
│   ├── auth.html
│   ├── app.html
│   ├── account.html
│   ├── css/
│   ├── js/
│   └── assets/
├── .gitignore
├── LICENSE
└── README.md
```

## Limitations

This is a static-analysis tool, not a full compiler or language server.

- Call resolution is best-effort. Dynamic dispatch and highly dynamic code cannot always be resolved statically.
- Chained receivers such as `self.repo.save()` and types inferred from function return values may remain unresolved.
- JavaScript test detection is limited because many test frameworks use anonymous callbacks rather than named test functions.
- Search is currently based on symbol names and qualified names; there is no natural-language or semantic search.
- Git history and pull-request analysis are not implemented.
- The application currently uses SQLite and is intended primarily for local/demo use.
- There is no email delivery service for password resets. In development, the reset token is returned by the API so the flow can be tested without an SMTP service. A public deployment should replace this with an email-based reset flow.

## Why build this?

When joining an existing project, one of the first problems is figuring out how the pieces fit together. Searching for a function name can tell you where a symbol appears, but it does not necessarily tell you which definition is actually being called or what depends on it.

Codebase Navigator focuses on that structural layer: **files → symbols → imports → calls → dependencies**.

The project was tested against real repositories while improving the resolver, with regression tests added for the cases that caused incorrect or missing dependency edges. The reasoning behind those changes is documented in [`docs/ENGINEERING.md`](docs/ENGINEERING.md).

## License

MIT. See [`LICENSE`](LICENSE).
