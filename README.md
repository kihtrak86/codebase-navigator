# Codebase Navigator

Codebase Navigator is a static-analysis tool for exploring and understanding unfamiliar codebases.

Give it a GitHub repository URL and it analyzes the source code, builds a dependency graph, and provides a web interface for exploring files, symbols, imports, and function relationships.

It currently supports **Python, JavaScript, JSX, TypeScript, and TSX**.

## Features

* Clone and index GitHub repositories
* Parse Python using Python's built-in `ast` module
* Parse JavaScript, JSX, TypeScript, and TSX using a custom parser
* Resolve imports and function/method calls across files
* Explore files, classes, functions, and methods
* View callers and callees for individual symbols
* Find related tests for a symbol
* Trace call paths between two symbols
* Run breadth-first impact analysis
* Visualize local dependencies with D3.js
* Store indexed repositories and user accounts in SQLite
* Authenticate users with password hashing and signed sessions

The project does **not** use an LLM. Analysis is performed directly from the source code, making the results deterministic for the same input.

## How It Works

The indexing pipeline follows these steps:

```text
GitHub Repository
       │
       ▼
     Clone
       │
       ▼
     Parse
   ┌───┴───────────────┐
   │                   │
Python AST       JS/TS Parser
   │                   │
   └─────────┬─────────┘
             ▼
     Import Resolution
             │
             ▼
      Dependency Graph
             │
             ▼
           SQLite
             │
             ▼
        Web Interface
```

The main challenge is resolving relationships between symbols.

For function and method calls, the resolver first looks at local bindings, imports, and known receiver types. When a call cannot be resolved more precisely, it falls back to repository-wide name matching. This allows the graph to remain useful when the same function or class name appears in multiple files.

## Tech Stack

### Backend

* Python 3.12+
* FastAPI
* SQLite
* SQL
* pytest

### Parsing & Analysis

* Python `ast`
* Custom JavaScript / TypeScript parser
* Import and call resolution
* Dependency graph construction

### Authentication

* PBKDF2 password hashing
* Starlette signed-cookie sessions

### Frontend

* HTML
* CSS
* Vanilla JavaScript
* D3.js

There is no frontend build step.

## Getting Started

### Requirements

* Python 3.12 or newer
* Git

### 1. Clone the repository

```bash
git clone <repository-url>
cd codebase-navigator
```

### 2. Set up the backend

```bash
cd backend
python -m venv .venv
```

Activate the virtual environment.

**Windows:**

```powershell
.venv\Scripts\activate
```

**macOS / Linux:**

```bash
source .venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

### 3. Start the application

```bash
python -m uvicorn app.main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

The frontend is served directly by FastAPI, so no separate frontend development server is required.

Create an account and provide a GitHub repository URL to begin indexing.

## Configuration

### Session Secret

For local development, the application can generate a session secret automatically.

To keep sessions valid across server restarts, set `CODENAV_SECRET_KEY` to a long random value.

**PowerShell:**

```powershell
$env:CODENAV_SECRET_KEY="your-random-secret"
```

**macOS / Linux:**

```bash
export CODENAV_SECRET_KEY="your-random-secret"
```

## Running Tests

Install the development dependencies:

```bash
cd backend
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
pytest
```

Tests cover:

* Python parsing
* JavaScript and TypeScript parsing
* Import resolution
* Function and method resolution
* Nested functions and scope handling
* Dependency graph construction
* Repository indexing
* API endpoints
* Authentication and account operations

## Project Structure

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

Codebase Navigator is a static-analysis tool rather than a compiler or language server.

Some limitations are expected:

* Dynamic dispatch and highly dynamic code cannot always be resolved statically.
* Chained receivers such as `self.repo.save()` may remain unresolved when their types cannot be inferred.
* Types inferred from function return values are not always available to the resolver.
* JavaScript test detection is limited for frameworks that rely heavily on anonymous callbacks.
* Search is currently based on symbol names and qualified names rather than natural-language or semantic search.
* Git history and pull-request analysis are not currently supported.
* SQLite is used as the application database and is intended primarily for local or small-scale deployments.
* Password-reset emails are not sent. In development, the reset token is returned by the API so the complete flow can be tested without an email service. A production deployment should replace this with an email-based reset mechanism.

## Project Focus

Understanding an unfamiliar codebase often starts with searching for function names and opening files one by one. That works for small projects, but becomes difficult when relationships span multiple files.

Codebase Navigator focuses on the structural relationships that are harder to see through simple text search:

```text
Files
  ↓
Symbols
  ↓
Imports
  ↓
Calls
  ↓
Dependencies
```

The project was developed with a focus on improving static call and import resolution and adding regression tests for cases that previously produced incorrect or missing dependency edges.

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE) for details.
