# Engineering Notes

This is the full, unedited build narrative for
[Codebase Navigator](../README.md) — every round of "index a real repo,
find a real bug, fix it, add a regression test," in order. It's kept
separate from the main README so the README can lead with a pitch; this
document is for anyone (an interviewer, a future contributor, future-me)
who wants the real story of how the resolution logic got as accurate as
it is, including every wrong turn along the way.

**Verification note (added when this document was split out of the main
README):** several rounds below note that `pytest` couldn't be run in the
sandbox that round was written in, and ask the reader to verify it
themselves. That verification has since been done: **all 69 tests pass**
in a real run. Those in-line caveats are left in place rather than edited
out, because the honest, in-the-moment uncertainty they record is part of
the actual history — this note just confirms how it resolved.

---

## Why this stack instead of the draft's exact stack

The draft calls for Tree-sitter + PostgreSQL + pgvector + OpenAI. I built the
same architecture with the same data model, but swapped in dependency-free
equivalents so the whole thing runs immediately with no API keys, no DB
server, and no native parser builds:

- **`ast` for Python, regex + brace-counting for JS/JSX/TS/TSX, instead of
  Tree-sitter.** Same job (files → classes/functions/imports/calls) for two
  language families using only the standard library. Each parser lives in
  its own file (`python_parser.py`, `js_parser.py`) and produces the same
  `ExtractedFile`/`ExtractedSymbol` shape (`common.py`), so
  `graph_builder.py` dispatches on file extension and otherwise doesn't
  care which parser ran. Adding a third language, or swapping either of
  these for real Tree-sitter grammars, means adding/replacing one parser
  module — the rest of the pipeline is unchanged.
- **SQLite instead of PostgreSQL+pgvector.** Same schema (see
  `app/db.py` — it's a direct copy of the draft's Repository/File/Symbol/
  Dependency/Test/CodeEmbedding tables). `code_embeddings` table exists but
  is unused until V3 wires up real embeddings.
- **No LLM call yet.** Search today is name/substring matching over symbols
  (`GET /api/repos/{id}/search?q=`), not the NL→retrieval→LLM pipeline in
  section 7 of the draft. The retrieval half (symbols + dependency graph +
  tests) is already built — the only missing piece is the LLM reasoning
  layer over that retrieved context, which needs an API key you'll want to
  supply yourself.

Everything is structured so those two swaps (Tree-sitter, Postgres+pgvector)
and the LLM layer are additive, not rewrites.

## Call resolution: what changed, and why

The first version resolved every call by simple name matching, repo-wide —
call it `foo(...)` anywhere, link to every symbol named `foo` anywhere. That
version's first real-world test (a full-stack JS repo) produced a wrong
answer: impact analysis on the server's `generateToken()` pulled in a
client-side React component (`Signup`) that had nothing to do with it,
because both files happened to reference something called `signup`.

Tracing the actual source showed the real mechanism was more specific than
"name collision": `Signup.jsx` does `const { signup } = useAuth();` — a
local variable destructured from a hook's return value, not an import, not
a call to anything defined elsewhere. The genuine `signup` function lives
*nested inside* `AuthProvider` in a completely different file, calling
`signupRequest` from yet another file via an ordinary import. None of that
structure existed for the resolver to use, so it fell back to a repo-wide
guess and got it wrong.

The fix is a three-step priority order, applied per call name inside a
symbol (`graph_builder.py`):

1. **Local shadowing.** Every symbol now records `local_bindings` — names
   bound by its own parameters or by a `const`/`let`/`var`
   declaration/destructuring inside its body. A call to one of these names
   gets **no edge at all**, resolved or not — it's known to be a local
   value, and guessing would just reintroduce the original bug. This is
   what stops the `signup` false-positive specifically.
2. **Import resolution.** Every import is now parsed into a structured
   `ImportBinding` (local name → module specifier → original name in that
   module), and `import_resolver.py` resolves the specifier to an actual
   file in the repo (relative-path resolution for JS, dotted-module +
   relative-dot resolution for Python). If it resolves, the call is linked
   to the specific top-level symbol with that name *in that file* — not
   searched for repo-wide. If the import resolves to something outside the
   repo (a package like `react` or `os.path`), the call is left
   unresolved on purpose: we know it's not a repo symbol, so a repo-wide
   guess would be strictly worse than nothing.
3. **Repo-wide name fallback**, only for whatever's left — a name that's
   neither locally shadowed nor traceable to an import (same-file sibling
   calls, or genuinely ambient names). This is the original strategy, now
   handling a much smaller slice of calls, and it's still the
   false-positive-tolerant default the draft's interview-prep section asks
   about ("how do you handle ambiguous/dynamic resolution?").

Re-running the exact same repo after this fix: `generateToken`'s impact
analysis now correctly stops at its three real callers
(`signup`/`login`/`resetPassword` in the server controller) and no longer
touches the client at all. And a real dependency that the *first* version
couldn't even see — `AuthProvider` calling the imported `signupRequest` —
now shows up correctly, because along the way I also found and fixed a
second, unrelated gap: concise arrow functions (`export const foo = (x) =>
expr;`, no `{ }` body) weren't being extracted as symbols at all, so
`signupRequest` and most of that repo's API-wrapper functions were
invisible to the graph. They're extracted now.

**What's still a known gap:** nested named functions were extracted next
(see below) and that upgrade itself surfaced a second round of the same
class of bug, worth recording since it's a good example of how deep this
rabbit hole goes.

## Nested functions, and a second round of the same bug

The natural next fix after the above was extracting nested named functions
as their own symbols — `AuthProvider`'s real `signup`/`login`
(`const signup = useCallback(...)` inside the component) weren't symbols at
all yet, only the enclosing `AuthProvider` was. Extracting them (one level
of nesting; `js_parser.py`'s `_extract_nested_functions`/
`_build_block_symbol`) immediately paid off two ways:

- `AuthProvider.signup` is now a real symbol, and `signupRequest`'s callers
  correctly show `AuthProvider.signup` specifically instead of the coarser
  "somewhere in `AuthProvider`".
- Finding it required first widening the block-function regexes to handle
  `const login = useCallback(async (...) => { ... }, [])` — a hook call
  *wrapping* the arrow function, which is how most real React
  components actually declare these, not the bare `const x = (...) => {}`
  the original regex expected.

But the very first full re-run of the fixed extractor **reintroduced the
original client/server false-positive** — through a different path than
before. The mechanism: `Signup.jsx`'s `handleSubmit` is itself nested
inside `Signup()`, and it calls the destructured `signup` — but `signup`
is destructured in the *enclosing* `Signup()` scope, not inside
`handleSubmit`'s own body. `local_bindings` was being computed per-symbol
from only that symbol's own parameters and its own body's declarations,
with no notion of inheriting what's shadowed in an enclosing closure. So
`handleSubmit`'s local_bindings didn't include `signup`, the call fell
through to repo-wide name matching again, and it landed on the server's
`signup` a second time.

The fix is scope inheritance: `_build_block_symbol` now computes each
symbol's full set of locally-shadowed names (own params + every
declaration in its body) and unions that into whatever was inherited from
enclosing scopes before passing it down to anything nested inside it. A
name shadowed several closures up now stays shadowed all the way down.
Verified by re-running the exact same repo: `generateToken`'s impact is
back to exactly the 3 real server-side symbols, and `signupRequest`'s
caller is now precisely `AuthProvider.signup`.

**The pattern worth naming explicitly:** every fix to call resolution so
far had been reactive — write the fix, test against a real repo, find the
next real case it doesn't cover, fix that. That pattern predicted its own
sequel: I went looking for the next round on purpose rather than waiting
for a third real repo to surface it, and found one in about a minute of
testing sibling functions.

## Round three: siblings leaking scope into each other

The natural next step after round two was extending nested-function
extraction to arbitrary depth instead of one level, since `_build_block_symbol`
already called itself recursively — turns out it already worked (a function
nested three levels deep extracts correctly, confirmed by testing
`Outer` → `middle` → `inner` directly). But testing the *scope inheritance*
logic more adversarially — two sibling nested functions, one of which
declares a local variable the other has no business seeing — found a real
bug in the round-two fix itself:

```js
function Outer() {
  const helperA = () => {
    const secret = getSecret();
    return secret;
  };
  const helperB = () => {
    return secret();   // a *different*, unrelated `secret` — a typo, or a
  };                     // same-named global — this should NOT resolve to
                          // helperA's local variable
}
```

`helperB`'s `local_bindings` incorrectly included `secret`, even though
`secret` is declared inside `helperA`'s own body, not in `Outer`'s shared
scope. The cause: computing a scope's "own declared names" from its full
body text — including the text that actually belongs to a nested child —
meant a name declared inside one nested function leaked into
`Outer`'s own scope_bindings, and from there into every sibling built from
it. This is the opposite failure mode from rounds one and two: not a
false positive (wrongly linking two unrelated things) but a **false
negative** — a call that should have resolved (to some other `secret`
elsewhere in the repo, or correctly stayed unresolved as ambiguous) got
silently swallowed instead, because the resolver believed it was a known
local value when it wasn't.

The fix (`_build_block_symbol` in `js_parser.py`) restructures the whole
nested-extraction pass into two steps instead of one: first detect *where*
every nested function is (pure line-range/name detection — this doesn't
need to know about scope at all), blank those regions out, and only then
scan what's left for what this scope actually declares. Inheritance still
flows downward through real enclosing scopes at any depth, but a name can
no longer leak sideways into siblings or upward into a parent. Re-verified
against the same NESTLINK repo: `generateToken`'s impact is still exactly
the 3 real server-side symbols (no regression), and the fix's real effect
shows up in the dependency count — 26 → 53 edges on the same repo, because
the leak had been silently suppressing real dependencies across the whole
codebase, not just in the one case that was directly tested for.

Nesting is now unwound at any depth (not just one level), and test mapping
is unchanged and still best-effort: `test_foo` is assumed to cover a
symbol named `foo`. Most JS test suites use `it("description", () => {})`
rather than named functions, so this will often find little in a Jest/
Mocha codebase — the graph itself still works fine, the "tests" panel
just tends to be sparse for JS repos.

## Running it

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Then open `http://localhost:8000` — you'll land on the public homepage;
sign up (any email/password ≥8 characters — there's no verification step,
this isn't meant to be internet-facing yet) to get to the app. The
frontend is served from the same process (no separate build step; plain
HTML/CSS/JS with D3 loaded from a CDN, not the React/Tailwind frontend
from the draft, to keep this runnable with zero `npm install`).

Frontend layout (three pages, split into separate files as of round
eight — see below):

```text
frontend/
  index.html     public homepage
  auth.html      sign in / sign up / forgot / reset
  app.html       the authenticated app
  account.html   account settings (profile, email, password, delete)
  css/           tokens.css, base.css, home.css, auth.css, app.css, account.css
  js/            common.js (shared), home.js, auth.js, app.js, account.js
  assets/        logo.svg, hero-graph.svg, auth-mesh.svg
```

**Set `CODENAV_SECRET_KEY` if you want sessions to survive a restart** —
without it, a random secret is generated each time the server starts,
which signs everyone out on every reload:

```bash
export CODENAV_SECRET_KEY="something-long-and-random"
```

**If you have an existing `codenav.db` from before this round**, delete
it before starting — the `repositories` table now requires a `user_id`
column that a pre-existing database won't have, and there's no migration
system yet (consistent with how every earlier schema change in this
project has been handled: this is dev tooling, not something with
production data to preserve across versions).

To index a repository, sign in, then paste a GitHub URL (or a local path —
the indexer just does `git clone`, so any git-reachable URL works) into
the box at the top and click **Index repository**. Cloning is `--depth 1`,
so large repos index fast but you lose history (fine for V1; V4's
git-history feature will need a full clone). Every repo you index is tied
to your account — signing in as a different user shows a different,
independent repo list.

## API surface

**Auth** (`auth_routes.py`, no session required to call these):
- `POST /api/auth/signup` `{email, password}` → creates an account and
  signs you in (password must be ≥8 characters; email must look like an
  email; duplicate emails are rejected)
- `POST /api/auth/login` `{email, password}` → signs in
- `POST /api/auth/logout` → clears the session
- `GET /api/auth/me` → the signed-in user, or `null`
- `POST /api/auth/forgot-password` `{email}` → always returns the same
  message regardless of whether the email has an account; also returns
  `dev_reset_token` (the raw reset token) since no email service is
  configured yet — see "Round six" below
- `POST /api/auth/reset-password` `{token, password}` → sets a new
  password from a valid, unused, unexpired reset token
- `POST /api/auth/change-password` `{current_password, new_password}` →
  session required; re-verifies the current password first
- `GET /api/auth/account` → profile plus a rollup of repos/files/symbols/
  dependencies owned by this account
- `POST /api/auth/change-email` `{new_email, current_password}` →
  re-verifies the password; rejects an address already in use
- `POST /api/auth/delete-account` `{current_password, confirm}` →
  `confirm` must be the literal string `DELETE`; deletes the user and
  cascades to every indexed repository, then clears the session
- Login is rate-limited to 5 failed attempts per email per 15 minutes
  (429 once exceeded, regardless of whether the next attempt would have
  been correct)

**Everything else requires a valid session** (`routes.py`) and is scoped
to the signed-in user — a repo or symbol ID that belongs to someone else
returns 404, not their data:
- `POST /api/repos/index` `{url}` → clones + parses + builds the graph
  (re-indexing the same URL replaces your previous copy of it rather than
  duplicating it; two different users indexing the same public repo get
  independent copies)
- `GET /api/repos` → your indexed repos with summary stats
- `DELETE /api/repos/{id}` → remove a repo and everything derived from it
- `GET /api/repos/{id}/tree` → nested folder/file tree with rolled-up
  counts; `?flat=true` for the flat file list
- `GET /api/repos/{id}/files/{file_id}/symbols` → symbols in a file
- `GET /api/repos/{id}/search?q=` → name-based symbol search
- `GET /api/symbols/{id}` → symbol detail + callers + callees + tests
- `GET /api/symbols/{id}/impact?max_depth=5` → BFS upstream impact analysis
  (mirrors the draft's "what could be affected if I change this?" example)
- `GET /api/symbols/{id}/graph?depth=2` → a bounded node/edge subgraph
  centered on this symbol (both directions, capped at 60 nodes), for the
  Graph tab's D3 visualization
- `GET /api/symbols/{source}/path/{target}` → shortest call path between
  two symbols (the draft's "path tracing" capability)

## Project layout

```
backend/
  app/
    main.py              FastAPI app: session middleware, mounts auth +
                          API routers, serves the static frontend
    db.py                SQLite schema: users, repositories (now user-scoped),
                          files, symbols, dependencies, tests, code_embeddings
    auth.py              password hashing (stdlib PBKDF2), session helpers,
                          get_current_user FastAPI dependency
    indexer/
      clone.py            git clone + file walking
      python_parser.py    ast-based extraction → ExtractedFile/ExtractedSymbol
      js_parser.py         regex/brace-counting extraction for JS/JSX/TS/TSX,
                            including nested named functions at any depth
                            with scope-correct binding inheritance
      common.py            shared ExtractedFile/ExtractedSymbol/ImportBinding dataclasses
      import_resolver.py   resolves an import specifier to a file in the repo
      graph_builder.py     ties files together, resolves calls (local shadow →
                            import → name fallback, capped at
                            AMBIGUOUS_NAME_THRESHOLD candidates), persists
                            per-user, upserts by (user, URL)
      analysis.py          callers/callees/impact/path-tracing/graph-subgraph queries
    api/
      routes.py          repo/file/symbol endpoints, all auth-gated and
                          ownership-checked
      auth_routes.py     signup/login/logout/me
  tests/
    conftest.py           shared fixtures: isolated temp SQLite DB per test,
                           a signed-up test_user, a fresh authed_client per
                           test (never a shared TestClient -- it keeps a
                           cookie jar), and a helper that builds real local
                           git repos so tests exercise actual `git clone`
                           without the network
    test_python_parser.py
    test_js_parser.py       includes a dedicated regression test for each
                             real bug found so far (self-call, missing
                             concise arrows, hook-wrapped arrows, local
                             shadowing, nested-function depth, sibling leak)
    test_import_resolver.py
    test_graph_builder.py   end-to-end: clone → parse → resolve → persist,
                             including the draft's exact example journey,
                             every cross-file false-positive/negative
                             scenario, and the ambiguity-threshold fix
    test_api.py             HTTP-layer tests: auth flow, per-user data
                             isolation, and every endpoint via FastAPI's
                             TestClient
  pytest.ini
  requirements-dev.txt     requirements.txt + pytest + httpx
frontend/
  index.html             homepage (public) → auth modal → app shell
                          (repo list with delete → file tree → symbol
                          detail with Overview/Called by/Calls/Tests/
                          Impact/Graph tabs, graph rendered with D3)
```

## Running the tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

44 tests, covering every language parser, import resolution, the full
indexing pipeline end-to-end against local fixture repos (no network
needed), and the HTTP API. This exists because the manual process that
found every real bug so far — clone a real repo, eyeball the output,
notice something's wrong, fix it, re-run the same manual checks by hand —
doesn't scale and doesn't stop a fix from quietly breaking. Each bug
described in the sections above now has a dedicated test named after it
(e.g. `test_sibling_nested_functions_do_not_leak_scope`), so a regression
shows up as a failing test instead of a wrong number in someone's browser
days later. I confirmed the tests actually catch what they claim to: I
temporarily reintroduced the round-three sibling-leak bug by disabling one
line and re-ran the suite — the two tests written for that exact bug failed
immediately, everything else stayed green, and restoring the fix brought
the suite back to 36/36. That's the check that matters more than the count.

This was the draft's own final checklist calling for "meaningful automated
tests" as a deliverable, not just a nice-to-have — it existed as a gap
until now (everything up to this point had been verified by hand, which is
also worth being upfront about: manual verification is real evidence, but
it isn't repeatable the way these are).

## What's genuinely tested vs. what isn't

Beyond the automated suite above, I also ran this against real/realistic
cases outside of what's captured in fixtures, re-running each after every
resolution-logic change (five rounds total: initial JS support, import
resolution + local shadowing, nested-function extraction, the scope-
inheritance fix that extraction turned out to need, and the sibling-leak
fix that fix turned out to need) to confirm each change actually moved the
output and didn't regress the others:
1. `navdeep-G/samplemod`, a small real Python package on GitHub.
2. A full-stack JS repo (Express + Mongoose backend, React/Vite frontend,
   37 files) — this is what turned up the name-collision bug, the
   missing-concise-arrow-functions gap, the hook-wrapped-arrow-function
   regex gap, the round-two false-positive from missing scope inheritance,
   and the round-three false-negative from that fix leaking scope between
   siblings, all described above and now covered by fixtures in
   `test_graph_builder.py` and `test_js_parser.py`.
3. The constructed fixture repo that reproduces the draft's exact example
   journey (`checkout() → process_payment() → validate_cart()/
   calculate_total()/charge_card()`, with `test_process_payment` and
   `test_checkout`) — now `test_draft_example_journey_python` in the suite,
   rather than a one-off manual script.
4. `django/django` at HEAD (~3,000 Python files, 38,562 symbols) — the
   large-repo stress test flagged as untested in every previous round of
   this README. See "Round four" below for what it found.

## Round four: 1,040,997 dependency edges for 38,562 symbols

This was step 1 of the previous round's next-steps list: point the tool at
something genuinely large and see what breaks. It didn't take long.
Indexing `django/django` produced over a million dependency edges — an
average of 27 per symbol — which isn't "somewhat noisy," it's a graph
that's useless for its actual purpose. Impact analysis on almost anything
would show a large chunk of the codebase as "affected."

The cause was exactly what earlier rounds' documentation predicted but
hadn't measured: `__init__` alone is defined 880 separate times across
unrelated classes in Django's own codebase (every class with a constructor
has one). Priority step 3's repo-wide name fallback doesn't know that —
it sees an unresolved call named `__init__` and links it to *every*
`__init__` in the repo. Same story for `__str__` (414), `setUpTestData`
(322), `setUp` (307), and dozens more generic/dunder-style names.

**The fix:** `AMBIGUOUS_NAME_THRESHOLD` in `graph_builder.py`. A name with
more candidates than the threshold (10) is left unresolved by the fallback
entirely, rather than linked to all of them — the same "known-unresolvable
beats a guess" stance already used for external imports. The threshold
comes from the actual distribution measured on Django: 25,137 symbol names
are unique, 2,046 have 2–3 legitimate duplicates, 510 have 4–8 — collisions
above ~10 are overwhelmingly generic names, not genuine repo-specific
name reuse worth linking. Re-indexing after the fix: **128,773 edges**, an
8x reduction, and indexing itself got faster too (15.0s → 10.6s, fewer rows
to write). Verified this didn't regress anything by re-running the full
test suite (still 38/38) and re-checking NESTLINK's `generateToken` impact
(still exactly the 3 correct symbols, dependency count unchanged at 53 —
nothing in that repo has anywhere near 10 same-named collisions, so the
threshold has zero effect on it, as it should).

**What this fix does *not* solve, and why it can't:** even after the fix,
`QuerySet.filter`'s impact analysis at depth 2 touches 2,204 symbols across
326 files. Checking why: there are 7 distinct `filter` methods on unrelated
classes in Django (the famous ORM one, but also a logging filter, a
template-library filter registration, and others) — 7 is comfortably under
the threshold, so all 7 are legitimate fallback targets, and the extractor
has no way to tell `some_queryset.filter(...)` from `some_logger.filter(...)`
apart, because it only ever captures the attribute name, never the
receiver's type. That's a fundamentally different, harder problem than the
one this round fixed — real disambiguation would need type inference this
parser doesn't attempt (see the Python/JS parsers' own "best-effort" notes
on attribute calls). Worth being honest that "the explosion is fixed" and
"impact analysis is precise at scale" are two different claims, and only
the first one is true yet.

## Round five: accounts, a homepage, and graph visualization

Three additions in one round, all outside what the draft itself scoped
(it describes a single-session portfolio project, not a multi-user
product) but necessary for this to be something more than one person's
local tool.

**Accounts.** Every repository now belongs to a user
(`repositories.user_id`), enforced at the query level, not just hidden in
the UI: every repo/file/symbol endpoint joins through `repositories` to
check `user_id` matches the signed-in session before returning anything,
so a guessed or incremented ID for someone else's repo 404s instead of
leaking data. Passwords are hashed with PBKDF2-SHA256 (200,000 iterations,
random salt per user) — stdlib-only (`hashlib`), no new dependency for
something this security-sensitive. Sessions are Starlette's built-in
signed-cookie `SessionMiddleware` (needs `itsdangerous`, now in
`requirements.txt`) rather than a server-side session table, which is
plenty for this scale and keeps the stack from growing a new moving part.
One real limitation, stated plainly: without `CODENAV_SECRET_KEY` set, the
session-signing secret is regenerated randomly every time the server
restarts, which logs everyone out. Fine for local use, not fine for
anything meant to stay up — see "Running it" below.

**Homepage.** A public landing page at `/` for anyone not signed in
(client-side gated: `boot()` calls `/api/auth/me` and switches between the
homepage and the app shell based on the result), with sign-up/sign-in as a
modal rather than separate pages, since there's nothing else on this site
yet to navigate between.

**Graph visualization.** A new `GET /api/symbols/{id}/graph` endpoint
(`analysis.build_symbol_graph`) and a D3-rendered force-directed graph in
a new "Graph" tab on the symbol detail view. Deliberately scoped to a
*bounded neighborhood* of one symbol (both directions, depth 2 by default,
capped at 60 nodes) rather than attempting to render an entire repo's
graph — round four's Django numbers (128,773 edges even after the
ambiguity fix) make it obvious that "visualize the whole repo" isn't a
readable graph, it's a hairball. Centering on one symbol and capping node
count is the same instinct as the ambiguity threshold: a bounded, honest
view beats an unusable complete one.

**UI: black and white only, on request.** The previous round's colored
theme (orange/violet/teal accents differentiating symbol types) is gone —
every color in the interface is now literally black, white, or a shade of
gray, including the graph visualization. Symbol types are distinguished by
shape/fill instead of hue: functions get a solid white glyph, classes an
outlined one, methods a dashed gray one — the same distinction the color
coding used to carry, just moved to a channel that survives grayscale.

**Tested the same way as every other round:** all 44 tests pass (8 new
ones covering signup/login/logout, duplicate-email and wrong-password
rejection, auth-required-on-every-endpoint, and the cross-user isolation
test that actually tries to access user A's repo and symbols as user B and
checks for 404 rather than data). The full auth→index→search→symbol→graph
flow was also run over real HTTP end-to-end (not just through the test
client) to confirm session cookies actually persist across requests the
way a browser would send them, and that unauthenticated/logged-out
requests are rejected with 401 rather than silently returning empty data.

## Round six: account hardening, and a UI pass

Picked up item 2 from round five's own next-steps list (session/account
hardening), plus general UI polish — homepage, auth, and a few small
navigation features. Call resolution (item 1, still the top of the list)
wasn't touched this round.

**Password reset.** `password_reset_tokens` table: a random 32-byte token
is generated, hashed with SHA-256, and stored with a 30-minute expiry;
only the hash is persisted, same "don't store the recoverable secret"
principle as password hashing itself. `POST /api/auth/forgot-password`
always returns the same response shape whether or not the email has an
account, so the endpoint can't be used to enumerate registered emails.
**Honest limitation:** there's no email-sending infra configured (no SMTP,
no provider API key), so rather than silently discarding the token behind
a response that claims success — which would make the feature untestable
end-to-end — the raw token is returned directly in the response body
(`dev_reset_token`) and the frontend surfaces it inline with a "no email
service configured" note. A real deployment would email a reset link and
drop that field from the response instead.

**Login rate limiting.** In-memory, per-email sliding window (5 failed
attempts / 15 minutes) in `auth.py` — same tradeoff as the session secret:
resets on restart, no new infra (no Redis, no persisted attempt table).
Keyed by email rather than IP since the threat is credential-stuffing one
account, and this deployment has no reverse-proxy layer to get a real
client IP from yet. A successful login clears the counter for that email.

**In-app password change.** `POST /api/auth/change-password` (session
required, current password re-verified before the new one is set) — an
account menu on the app header replaces the old plain "sign out" button.

**Tested the same way as every other round:** all backend logic changes
have dedicated tests (rate-limit lockout and its reset, forgot-password
non-enumeration, reset-token success/reuse/invalid-token rejection,
change-password's auth requirement and wrong-current-password rejection) —
see `test_api.py`. Note for whoever runs this next: the sandbox this round
was done in has no network access, so `pytest` itself could not be
executed here — every new test was written against the same fixtures and
patterns as the existing 44, and every changed file was confirmed to
compile (`python -m py_compile`), but **run `pytest` yourself before
trusting this the way the rest of this README's numbers are trusted.**
That's a real gap in verification, stated as plainly as every other one in
this document.

**UI pass** (`frontend/index.html`, still vanilla JS + D3, still strictly
black/white/gray):
- Homepage: added a "how it works" 3-step section with a worked example
  panel (the draft's own `checkout()` impact-analysis journey), and a
  "built for" section listing the four user personas from the draft's
  target-user list.
- Auth modal now has four modes (`login` / `signup` / `forgot` / `reset`)
  instead of two, sharing one form and toggling which fields show.
- Graph tab: added a legend (glyph meaning for function/class/method + the
  solid-white "selected symbol" marker), since the monochrome scheme
  doesn't explain itself to a first-time viewer.
- Repo cards: added a re-index button (reruns indexing against the same
  URL, e.g. after the repo's remote has new commits) next to the existing
  delete button.
- `/` focuses the symbol search box when no input is currently focused.

## Round nine: file-manager tree, and a real account module

**Nested file tree.** `GET /repos/{id}/tree` previously returned a flat
list of full paths (`src/auth/login.py`, one row per file) and the UI
printed them as-is — fine for a fixture repo, unreadable for anything
real. It now returns a nested folder/file structure. Directories sort
before files, each group alphabetically, and each directory carries a
rolled-up `file_count` and `symbol_count` from everything beneath it, so a
collapsed folder still tells you how much is inside. That rollup is why
the nesting is built server-side rather than left to the client. The old
shape is still available as `?flat=true` — the flat list is genuinely the
better format for some callers, and keeping it meant the change didn't
have to break anything.

Frontend renders it as an actual file-manager tree: collapsible folders
with rotating carets, indentation by depth, expand-all/collapse-all, and
per-row counts. Top-level folders start open and deeper ones start
closed, so a deep project doesn't open as hundreds of rows. Folders and
files are distinguished by icon *shape* (a tab-topped rectangle vs. a
portrait one), not colour — same rule the symbol-type glyphs already
follow.

**Account module.** The old "account" was one dropdown item opening a
change-password modal. It's now `/account.html`, a real page with four
sections:
- **Profile** — email, member-since date, and a rollup of what the
  account actually holds (repos / files / symbols / dependencies). The
  rollup exists so "delete everything" isn't an abstract button.
- **Email address** — `POST /auth/change-email`. Changing the address you
  log in with is a credential change, so it re-verifies the password, the
  same as changing the password does, and rejects an address already in
  use.
- **Password** — the existing `change-password` endpoint, moved here.
- **Delete account** — `POST /auth/delete-account`. Requires the password
  *and* typing DELETE (validated server-side by a pydantic validator, so
  it's a 422 before the handler ever runs, not just a client-side
  courtesy), plus a browser confirm on top. Clears the session on success
  — the cookie shouldn't outlive the account. Repositories, files,
  symbols and dependencies go with the user row via `ON DELETE CASCADE`
  (`PRAGMA foreign_keys = ON` is set in `db.py`, so that cascade actually
  fires — worth stating, since SQLite silently ignores foreign keys
  without it).

**Tests:** 12 new in `test_api.py` — tree nesting and count rollup as a
unit test on the pure `_build_file_tree` function (shape verified without
standing up a repo), the endpoint's nested-vs-flat behaviour, tree access
to another user's repo returning 404, account overview totals and its
auth requirement, and for each destructive operation both the happy path
and its guards (wrong password, address in use, missing confirmation).
The delete test asserts the cascade actually emptied `repositories` and
`symbols`, rather than trusting the schema.

**Verification, same caveat as rounds six through eight:** no network in
this sandbox, so `pytest` itself could not run. Everything compiles, the
tree builder was executed directly against the fixtures above, and both
new UI surfaces were driven in headless Chromium — the tree's
expand/collapse verified by counting visible rows (4 → 6 → 2), and the
account page rendered against a stubbed backend with the delete guards
confirmed to block on a wrong confirmation and on a dismissed browser
confirm. **Run `pytest` yourself before trusting the new backend tests.**

## Round eight: frontend restructure and UI pass

Frontend only — no backend or parser changes this round. The single
944-line `index.html` was split into three real pages with separate CSS
and JS files, and the auth flow moved out of a modal onto its own page.

**File split.** One monolith became: three HTML pages (`index`, `auth`,
`app`), five CSS files (`tokens` → `base` → per-page), and four JS files
(`common.js` plus one per page). The load order matters: `tokens.css`
defines the custom properties everything else reads, `base.css` holds the
resets and the components that appear on more than one page (buttons,
inputs, the symbol-type glyphs, the modal shell), and each page pulls only
its own stylesheet on top. `common.js` holds what all three pages need —
the `api()` fetch wrapper, `esc()`, `typeGlyph()`, the inline logo markup,
and the two auth-guard helpers.

**Routing replaces view-toggling.** The old build kept the homepage, the
auth modal, and the app in one document and toggled `display`. Now
`/auth.html` is a real page (deep-linkable as `?mode=login|signup|forgot|reset`,
and the mode is written back to the URL as you switch, so a refresh keeps
the form you were on), `app.html` calls `requireAuth()` and bounces to
`/auth.html` if there's no session, and `auth.html` does the reverse —
if you already have a session it sends you straight to the app.

**A real bug this caught.** First render of `auth.html` showed no "Forgot
password?" link at all: the session check ran *before* `setAuthMode()`, so
when `/api/auth/me` failed the mode was never applied and the form stayed
in its default half-hidden state. Under a healthy backend you'd never see
it; with the backend down you'd get a login page with pieces missing. Fixed
by setting up the form first and treating the session check as best-effort
on all three pages — `app.html` now shows "couldn't reach the server"
instead of bouncing you to a login page you may not need, and the homepage
just keeps its signed-out nav. Found by driving the real pages in a headless
browser, not by reading the diff.

**Visual work.** A logo built from the app's own glyph vocabulary (filled
square = function, outlined = class, dashed = method, wired together as a
three-node graph) — the mark is a literal miniature of what the product
shows you, and doubles as the favicon. The homepage hero is now a
two-column split with a rendered SVG call-graph illustration of the
draft's own `checkout()` → `process_payment()` example, including the
"3 functions · 2 files · 4 tests" impact annotation. `auth.html` is a
split screen: form on the left, full-bleed generated graph-mesh art on the
right under a gradient scrim. Both illustrations are generated SVG rather
than photography — the palette is strictly black/white/gray and a stock
photo would fight it.

**Verified by rendering, not just reading.** Every page was loaded in
headless Chromium at 1440px and 390px, screenshotted, and checked: all
four auth modes switch correctly and update the URL, the mobile layout
collapses the art panel, and the console is clean apart from the expected
`/api` failures under `file://`. A static cross-check also confirms every
`getElementById` in the JS resolves to an element that exists, every
`onclick` handler in the HTML is defined, every local `href`/`src` points
at a real file, and no page has duplicate IDs. Two render bugs were caught
and fixed this way: the hero SVG's right-hand annotations were clipped by
a too-narrow `viewBox`, and the logo wordmark inherited an underline from
the global `a` rule.

**Backend untouched.** `StaticFiles` already serves subdirectories, so the
new `css/`, `js/`, and `assets/` folders needed no change to
`main.py`. The graph-builder suite was re-run afterward anyway (12/12) to
confirm nothing drifted.

## Round seven: attribute-call receiver disambiguation

Picked up item 1 from round six's next-steps list — the top of that list,
untouched since round four's ambiguous-name threshold. Call resolution now
does light, best-effort receiver-type tracking for attribute calls
(`receiver.method()`), instead of always falling back to matching `method`
against every same-named symbol repo-wide.

**What's tracked, per parser, single linear pass over a function body, no
control-flow awareness:**
- `self.foo()` / `cls.foo()` (Python) and `this.foo()` (JS/TS) → typed to
  the *enclosing class*.
- `x = ClassName(...)` (Python, `ClassName` PascalCase by convention) /
  `const x = new ClassName(...)` (JS/TS) → typed to `ClassName` for
  every `x.method()` that follows, until `x` is reassigned to something
  else.
- Everything else — a chained attribute (`self.repo.save()`,
  `this.repo.save()`), a receiver assigned from a plain function call
  (`user = get_user(); user.save()`), or a name never assigned at all —
  stays untyped and falls through to the existing name-only resolution
  exactly as before. **Deliberately narrow**, same posture as everywhere
  else in this pipeline: a typed call either resolves against the
  *specific* class's method or is left unresolved entirely — it never
  falls back to the ambiguous repo-wide guess, because a known (if
  imperfect) receiver type is more informative than no receiver at all,
  and mixing the two would just reintroduce the noise this exists to
  remove.

**Concrete effect:** `qs = QuerySet(); qs.filter(...)` now resolves to
`QuerySet.filter` specifically. Before this round, `Logger.filter`,
`TemplateFilter.filter`, and every other class's `filter` method within
the ambiguity threshold would *all* show up as "potentially affected" by
a change to any one of them — this is the concrete Django scenario named
in the project's own known-gaps list from round four onward.

**New data flow:** `ExtractedSymbol` gained `typed_calls: list[tuple[str,
str]]` (receiver_type, method_name) alongside the existing `calls: list[str]`.
A call that lands in `typed_calls` is never also in `calls` — resolved via
receiver type alone, or not at all. `graph_builder.py` resolves
`typed_calls` first, against a repo-wide `qualified_name -> [symbol_id]`
map, capped by the same `AMBIGUOUS_NAME_THRESHOLD` as the existing
fallback (for the rare case two unrelated classes share a name).

**Tests:** 4 new end-to-end scenarios in `test_graph_builder.py` — the
Django-style `filter()` disambiguation in both Python and JS, `self.foo()`
resolving to the specific class, and a typed-but-unmatched receiver
(external/unknown class) staying unresolved rather than guessed. One
existing test (`test_class_and_methods_extracted_with_correct_parent`)
was updated, not reverted — `this.validate()` moving out of the generic
`calls` list and into `typed_calls` is the intended behavior change, not
a regression.

**Same network caveat as round six applies:** this sandbox has no network
access, so `pytest` could not be run here. Every changed file compiles
(`python -m py_compile`), and — because this round touches the resolution
core rather than an isolated feature — I went further than usual and
manually re-ran the *entire* existing `test_graph_builder.py`,
`test_python_parser.py`, and `test_js_parser.py` suites by importing and
calling each test function directly against a real SQLite DB (bypassing
only the `conftest.py` fixtures that require `fastapi.testclient`, which
needs the network to install). All 12 + 6 + 9 = 27 tests pass, including
every round-one-through-four regression case (local shadowing, import
resolution, sibling scope isolation, the ambiguity threshold). That's
real signal, but it's not the same as `pytest` itself passing — **run the
actual suite before trusting this the way the rest of this README's
numbers are trusted.**

**What this still doesn't fix** (be precise about the boundary): a
receiver that's itself a chained attribute (`self.repo.save()`), a
receiver typed from a plain function's return value
(`user = get_user(); user.save()`), or any type-narrowing that depends on
control flow (`if cond: x = A() else: x = B()`) all stay untyped, same as
before. Real type inference (or a real parser with actual scope/type
analysis, i.e. the Tree-sitter upgrade this whole parser stack is a
placeholder for) would close those — this is a targeted fix for the
single highest-volume false-positive pattern found in round four, not a
general type system.

## Suggested next steps (in the order I'd do them)

1. ~~**A fifth real repo**~~ — done, informally: a JS/TS-heavy repo of
   yours was indexed and run through the app outside this sandbox (which
   has no GitHub network access — see below). Result: ran clean end to
   end, no errors. **What this confirms:** the pipeline doesn't crash or
   choke on a real, non-fixture repo's actual JS/TS patterns. **What it
   doesn't confirm:** no impact-analysis or search *results* were
   spot-checked by hand, so this isn't evidence the resolution logic
   itself is more or less accurate on this repo — only that it runs. The
   Django-style `filter()` re-check from round seven (does typed-call
   resolution actually shrink the ambiguous-edge count on a repo that has
   the pattern?) is still open, and would need a Python-heavy repo with
   genuine same-named-method collisions to be meaningful — this repo
   being JS/TS-heavy doesn't exercise that specific number.
2. **A hand-checked pass on a real repo** — pick a handful of symbols you
   know well in that (or another) real repo, look at what the app says
   are their callers/callees/impact, and confirm it's actually right, not
   just error-free. This is the check rounds one through four actually
   did (each one found a real bug this way) — round eight so far hasn't.
3. **Chained-attribute and control-flow-aware typing** — the two biggest
   named gaps in what round seven doesn't cover (above). Both are
   reachable without a full type system: chained attributes need one more
   level of lookup (track `self.repo`'s type from `self.repo = RepoClass()`
   in `__init__`/constructor); control-flow awareness mostly needs "last
   assignment wins per branch, not per whole function" instead of a single
   linear scan.
4. ~~**Session/account hardening**~~ — password reset, login rate-limiting,
   and in-app password change landed in round six. Still open: no email
   verification, and the random session secret on restart (documented
   above) — both fine for local use, both things a real deployment would
   need before being internet-facing.
5. **Wire up the LLM layer** (`app/api/search_nl.py`, doesn't exist yet):
   take a question, run it through `/search`, pull in callers/callees/tests
   for the top matches, and pass that as context to an LLM call for the
   final natural-language answer with source citations — this is section 7
   of the draft and the main differentiator from "chat with GitHub." This
   was explicitly deferred this round rather than attempted without an API
   key to test against.
6. Everything else in the draft's V4 (git history, PR analysis, Docker,
   deployment) — none of that is started yet.

