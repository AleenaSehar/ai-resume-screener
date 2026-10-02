# Project Guide — AI Resume Screener

> **Maintenance note:** This file is meant to always reflect the current state of the
> project — architecture, roadmap, and decisions. Any time the codebase changes in a
> way that affects what's described here, this file gets updated in the same
> changeset. If you're reading this and something looks out of sync with the code,
> that's a bug in this doc — flag it.

Last updated: 2026-10-02 (auth + user accounts)

---

## 1. What this is

A single-purpose web tool: paste a job description and a resume (as text or a PDF
upload), and get back an AI-generated match analysis — a 0–100 score, a skills/
experience/education breakdown, matched/missing/bonus skills, strengths, gaps,
actionable suggestions, and a plain-English summary. Results can be exported as a
PDF report. Up to 5 resume PDFs can be screened against one job description at once
in **batch mode**, producing a ranked comparison table and a combined PDF report.
Every screening is saved to **history**, scoped per-browser via an anonymous
client-generated ID (no login), viewable and deletable from a History panel.
Optional **email+password accounts** let someone claim that browser's history
onto a durable login — signing in is never required to use the tool.

It's intentionally small: no build step on the frontend, one external AI
dependency (Google Gemini), one optional persistence dependency (Postgres, for
history and accounts — the core screening feature works with zero database at
all), and auth is self-rolled on that same database rather than a third-party
service.

---

## 2. Architecture

```
┌─────────────────────┐         ┌──────────────────────┐        ┌─────────────────┐
│   Browser            │  HTTPS  │  Backend (Vercel)     │  HTTPS │  Google Gemini   │
│   frontend/index.html│ ──────▶ │  backend/main.py      │ ─────▶ │  gemini-3.5-flash│
│   (static, no build) │ ◀────── │  FastAPI serverless   │ ◀───── │  (structured JSON)│
└─────────────────────┘  JSON   └──────────────────────┘  JSON  └─────────────────┘
      hosted on                        hosted on
       Netlify                          Vercel
```

- **Frontend**: one static HTML file (`frontend/index.html`) with inline CSS/JS, plus
  `frontend/config.js` for the one environment-specific value (the backend URL). No
  npm, no bundler, no framework. Deployed to **Netlify** as a static site.
- **Backend**: one FastAPI app (`backend/main.py`) — the core `POST /screen`;
  `GET /history`, `DELETE /history/{id}`, `DELETE /history` for screening
  history; and `POST /auth/signup`, `POST /auth/login`, `GET /auth/me` for
  accounts. Deployed to **Vercel** as a Python serverless function — Vercel
  auto-detects FastAPI from `requirements.txt` and runs `main.py` directly, no
  Dockerfile involved on that path.
- **AI**: Google Gemini (`gemini-3.5-flash`), called via the `google-genai` SDK.
  Gemini's structured-output mode (`response_json_schema`, built directly from the
  `ScreenResult` Pydantic model) guarantees the response matches the shape the
  frontend expects — no fragile "strip markdown fences and hope it's valid JSON"
  parsing.
- **No server-side session state, even with accounts.** Auth is JWT-based
  (`Authorization: Bearer <token>`, verified per-request) — there's no session
  store anywhere, consistent with every other part of this app being stateless.
  The only persistence is Postgres/Neon: a `users` table and a `screenings`
  table scoped by either a client-generated `anonymous_id` (no login) or a real
  `user_id` (logged in), with `user_id` always taking priority when both are
  present. If `DATABASE_URL` is unset, the app behaves exactly as if there were
  no database at all: `/screen` still works, `/history` returns `[]`, and
  `/auth/*` returns a clean `503` rather than silently doing nothing.

Why two hosts instead of one: Netlify only serves static files — it cannot run a
persistent/serverless Python process. Vercel could technically host both (it serves
static sites too), but the frontend went to Netlify because that's what was asked
for; splitting wasn't a technical requirement, just how the request was scoped. See
§6 for the full story of how each host was chosen.

---

## 3. Request walkthrough (step by step)

This is what happens for one "Analyze match" click, end to end:

1. **User input** — the user pastes a job description, and either pastes resume text
   or switches to the "Upload PDF" tab and drops/selects a PDF (client-side capped at
   2MB, validated for MIME type `application/pdf`).
2. **Client-side validation** (`analyze()` in `index.html`) — checks the JD is
   present and ≥50 characters, and that exactly one resume input (text or file) is
   provided and valid. A PDF is read via `FileReader.readAsDataURL()` and the base64
   payload (stripped of the `data:` URL prefix) is held in memory as `resumeFile`.
3. **Request** — a `fetch()` `POST` to `${API_BASE}/screen` with either
   `{ job_description, resume }` or `{ job_description, resume_file, resume_file_name }`
   as JSON. `API_BASE` comes from `window.API_BASE`, set in `config.js` based on
   hostname (localhost → local backend, anything else → the deployed Vercel URL).
4. **Backend validation** (`screen_resume()` in `main.py`) — re-validates everything
   the client already checked (never trust the client): JD length bounds, exactly one
   resume input, base64 decodes cleanly, decoded PDF ≤2MB. Any failure returns a 400
   with a specific `detail` message the frontend surfaces directly.
5. **Rate limiting** — `slowapi` caps `/screen` at 10 requests/minute per client IP
   (best-effort only on Vercel — see §7's caveat).
6. **Gemini call** — `client.models.generate_content()` with:
   - `system_instruction`: the recruiter persona/framing
   - `contents`: either the formatted text prompt (text-resume path), or a list of
     `[prompt_text, types.Part.from_bytes(pdf_bytes, mime_type="application/pdf")]`
     (PDF path) — Gemini reads the PDF natively, no separate text-extraction library
   - `response_mime_type="application/json"` + `response_json_schema=RESPONSE_SCHEMA`
     (derived from `ScreenResult.model_json_schema()`) to force schema-conformant JSON
7. **Response validation** — the JSON string in `response.text` is parsed and
   unpacked into `ScreenResult(**result)`. If Gemini's JSON doesn't match the schema,
   this raises and the client gets a 500 with the parse error, not a silent bad
   render.
8. **Error surfacing** — `genai_errors.APIError` (e.g. transient `503 UNAVAILABLE`
   during high demand, which happens periodically on the free tier) maps to a 502;
   the frontend shows the actual upstream error message rather than swallowing it.
9. **Render** (`renderResults()`) — builds the results DOM from the JSON: score
   ring, category bars (animated), skill tag groups, strengths/gaps/suggestions
   cards, summary. Auto-scrolls to the results (`scroll-margin-top` keeps content
   clear of the sticky nav — see the commit history for why this matters).
10. **Optional: Export as PDF** (`exportPDF()`) — takes the last rendered result
    (held in `lastAnalysisResult`) and builds a formatted PDF client-side with jsPDF
    (loaded from cdnjs), entirely in the browser. No backend involvement, no new
    request.

### 3b. Batch mode walkthrough

Batch mode reuses every piece above — it does not call a different backend
endpoint or add server-side logic. The only backend-facing difference is that
`/screen` gets called multiple times instead of once.

1. **Opt in** — on the "Upload PDF" tab, a checkbox ("Screen multiple resumes, up
   to 5") toggles `batchMode` and flips the file input to `multiple`. The dropzone
   switches from single-file display to a running file list (`batchFiles`, each
   entry `{ name, base64, status, error, result }`), capped client-side at
   `MAX_BATCH_FILES = 5`.
2. **`analyze()` branches early** — if `resumeMode === "pdf" && batchMode`, it
   delegates to `analyzeBatch(jd)` and returns, skipping the single-resume path
   entirely.
3. **Sequential loop, not parallel** (`analyzeBatch`) — a plain `for` loop calls
   `screenRequest({ job_description, resume_file, resume_file_name })` once per
   file, one at a time, `await`ing each before starting the next. This is
   deliberate: it was built while Gemini's free tier was under heavy load, and
   parallel (`Promise.all`) fan-out would have made that worse. At ~20–40s per
   call, 5 sequential calls stay comfortably under the existing 10/minute rate
   limit (at most one request in flight, ever) — see §6's decision log.
4. **Per-item failure isolation** — each loop iteration has its own `try/catch`.
   One resume hitting a Gemini 503 marks just that item `error` and the loop
   continues; it doesn't abort the batch. A progress panel
   (`renderBatchProgress()`) shows live status per file (pending/active/done/error).
5. **Ranked results table** (`renderBatchResults()`) — successful results sorted
   by score descending, failed/pending ones listed after. Each row expands to the
   *exact* same detail view as single mode: `buildResultDetailHTML(r, idPrefix)`
   was extracted from `renderResults()` specifically so both paths render
   identically, parameterized only by an id prefix (needed since bar-fill element
   ids would otherwise collide across simultaneously-expanded rows). Expanded-row
   state survives re-renders via `expandedBatchRows` (a `Set` of indices) — without
   this, retrying a failed item would collapse whatever the user had open.
6. **Per-item retry** (`retryBatchItem(i)`) — re-runs `screenRequest` for just one
   file using the JD captured at batch-start (`lastBatchJD`); does not touch the
   other candidates or re-run the whole batch.
7. **Combined export** (`exportBatchPDF()`) — one PDF, one candidate per page
   (`doc.addPage()` between candidates), built from the same `createPdfWriters()` /
   `writeCandidateReport()` helpers `exportPDF()` uses — no duplicated layout code
   between single and batch export.

### 3c. Screening history walkthrough

1. **Anonymous ID** (`getOrCreateAnonymousId()`) — on first load, generates a
   `crypto.randomUUID()` and stores it in `localStorage`
   (`resumeScreenerAnonymousId`); reused on every later visit. Wrapped in
   `try/catch` — if `localStorage` throws (private browsing, disabled storage),
   `ANONYMOUS_ID` is `null` and history silently becomes unavailable rather than
   crashing anything.
2. **Saved automatically, for free** — `ANONYMOUS_ID` is injected inside
   `screenRequest()` itself (`{ ...requestBody, anonymous_id: ANONYMOUS_ID }`),
   the single function every screening path (single, batch, retry) already
   calls. No per-call-site plumbing needed.
3. **Backend save** (`save_screening()` in `main.py`) — called at the end of
   `screen_resume()`, after a successful Gemini result, right before returning
   it. Best-effort: wrapped in `try/except Exception`, connects with
   `connect_timeout=5` (bounds a hung connection attempt so a database outage
   can never turn into a slow/stuck `/screen` request), and does nothing at all
   if `anonymous_id` or `DATABASE_URL` is missing. A save failure is logged and
   swallowed — `/screen`'s response to the user is completely unaffected either
   way.
4. **Viewing history** (`toggleHistoryPanel()` → `loadHistory()`) — a nav button
   opens `#history-panel` (a sibling of `#results`, sharing its `.results` class
   specifically to inherit `scroll-margin-top` and the `.visible` transition
   without redefining them) and fetches `GET /history?anonymous_id=...`.
5. **Rendering** (`renderHistory()`) — a table styled identically to the batch
   results table (`.batch-table`), one row per screening, expandable to the
   *exact* same `buildResultDetailHTML()` markup single mode and batch mode both
   use — the third reuse of that function, not a new one-off view.
   `expandedHistoryRows` (a `Set`, surviving re-renders) exists for the same
   reason `expandedBatchRows` does: deleting an entry re-renders the whole list,
   and without tracked state that would silently collapse whatever the user had
   open — the same bug class caught once already in batch mode, fixed here
   before it could be rediscovered.
6. **Delete** (`deleteHistoryEntry()` / `clearAllHistory()`) — call
   `DELETE /history/{id}` or `DELETE /history`, scoped by `anonymous_id` as a
   query parameter (there's no session to derive it from). The remove button
   reuses `.dropzone-remove` — the same bordered-square button style established
   for "remove/delete" everywhere else in the app.
7. **Backend read/delete endpoints** — `GET /history` returns `[]` if
   `DATABASE_URL` is unset (a bonus panel showing "no history" rather than an
   error when nobody configured a database), but a genuine `502` if the DB is
   configured and unreachable (unlike `/screen`, there's no underlying core
   feature for `/history` to protect — the request's entire job is fetching
   history, so a real failure is worth surfacing). Both `DELETE` endpoints
   return `404`/`503` rather than silently no-op-ing when the target row or the
   database itself doesn't exist.

### 3d. Auth walkthrough

1. **Sign up** (`handleAuthSubmit()` → `signupRequest()`) — email + password
   (8+ chars) via a single toggle-mode form (`#auth-panel`, no modal exists
   anywhere in this app) shared with login. The current browser's `ANONYMOUS_ID`
   is sent along automatically.
2. **Backend account creation** (`signup()` in `main.py`) — hashes the password
   with `bcrypt`, inserts the `users` row, and in the **same transaction**
   claims any existing anonymous history: `UPDATE screenings SET user_id = %s
   WHERE anonymous_id = %s AND user_id IS NULL`. The `user_id IS NULL` guard
   means a row can only ever be claimed once — if this browser is reused by a
   different account later, only still-unclaimed rows get swept up. A duplicate
   email is a clean `409`, not a raw database error leaking to the client.
3. **Session**: the response is `{ token, user }` — a JWT (`PyJWT`, 30-day
   expiry, no refresh mechanism), stored in `localStorage`
   (`resumeScreenerAuthToken`), **not** an httpOnly cookie (the frontend and
   backend are different origins — Netlify/Vercel — so cookie auth would need
   fragile cross-site cookie settings; this also matches the existing
   `ANONYMOUS_ID`-in-localStorage pattern already in this codebase).
4. **Every authenticated request** goes through `authFetch()` — a single
   wrapper (used by `screenRequest`, and all three history network functions)
   that attaches `Authorization: Bearer <token>` when present and, critically,
   calls `logOut()` automatically on any `401` response. This is the frontend
   half of the **hybrid authorization rule**: no `Authorization` header at all
   → fully anonymous, never an error; a header that *is* present but
   invalid/expired → always a clean `401`, everywhere (`decode_token()` in
   `main.py` enforces the backend half). A simpler "just ignore bad tokens"
   design was rejected because it would mean an expired token silently
   degrading to anonymous — a user's own screenings would quietly stop being
   saved to their account with no indication why. One consistent "your session
   died" signal, not per-endpoint bespoke handling.
5. **Scoping priority**: whenever both a valid token and an `anonymous_id` are
   present, `user_id` wins for every save and every `/history*` operation —
   `anonymous_id` is only ever the fallback for logged-out requests. `ANONYMOUS_ID`
   keeps being generated and sent unconditionally even when logged in: it's
   harmless (always overridden), and it's what a `/screen` call falls back to
   if a token happens to expire mid-session on a long-lived tab, rather than
   that save silently failing to attribute to anyone.
6. **Session restore on load** (`restoreSession()`) — if a token exists in
   `localStorage`, calls `GET /auth/me` through the same `authFetch()`; a `401`
   there triggers the same silent-logout path as any other endpoint, so a
   stale/expired token never leaves the UI looking logged-in while quietly
   failing every real request.
7. **Two real, pre-existing bugs were caught and fixed while building this,
   neither found by reading the code — both found by running it:**
   - The anonymous `GET/DELETE /history*` queries filtered only by
     `anonymous_id`, with no `AND user_id IS NULL` guard — meaning a claimed
     row (now owned by an account) was still visible to anyone who still had
     the old `anonymous_id` in their browser. Caught by the exact end-to-end
     test this feature's plan called for (claim a row, then check the old
     anonymous path no longer returns it).
   - CORS's `allow_methods`/`allow_headers` (set when `/screen` was the only
     endpoint) had never been widened when the history feature added `DELETE`
     endpoints — meaning the "Clear all"/delete-entry buttons had been
     **silently broken in the real deployed app** since that feature shipped
     (a real browser's preflight `OPTIONS` for `DELETE` was being rejected).
     This had gone unnoticed because every test for that feature used `curl`
     or a local backend directly — neither goes through browser CORS
     enforcement at all. Fixed alongside adding `Authorization` to
     `allow_headers` for this feature. Lesson: cross-origin behavior needs a
     real-browser check specifically, not just a passing `curl`/local test —
     see §9's testing conventions.
8. **`escapeHtml()` shipped alongside this feature, not before it.** This
   project has never had a real credential living in the browser until now;
   the pre-existing pattern of interpolating Gemini-generated/user-originated
   text straight into `innerHTML` (summary, verdict, skill/strength/gap text,
   filenames) went from cosmetic to a real token-theft vector the moment a JWT
   started living in `localStorage`. Applied in `buildResultDetailHTML`,
   `renderBatchResults`/`renderBatchProgress`, and `renderHistory` — wrapping
   just the string values, not a rewrite. First explicit security-regression
   test in this project (a mocked `<img src=x onerror=...>` summary, asserted
   to render as inert text) — worth keeping as a template for any future
   `innerHTML`-touching feature.

---

## 4. File-by-file reference

| File | Purpose |
|---|---|
| `backend/main.py` | The entire API: CORS, rate limiting, request validation, the Gemini call, screening-history persistence, auth (signup/login/JWT verification), error handling. One file by design — small enough not to need more structure yet. |
| `backend/requirements.txt` | Pinned dependencies. Versions matter here — see §7, the `anthropic`/`httpx` incompatibility bug is a cautionary example of why. |
| `backend/schema.sql` | The single source of truth for the `users` and `screenings` tables. Run once, manually, in Neon's SQL editor (or any Postgres) — not auto-applied on startup. README/GUIDE reference this file rather than duplicating the SQL inline. |
| `backend/Dockerfile` | Used by `docker-compose.yml` (local dev) and by Render if you deploy there via `render.yaml`. **Not** used by the Vercel deployment path — Vercel runs `main.py` directly via its Python runtime. |
| `backend/.env.example` | Template for local secrets. Copy to `backend/.env` (gitignored) and fill in `GEMINI_API_KEY` (required), `DATABASE_URL` (optional, history), and `JWT_SECRET` (optional, accounts — requires `DATABASE_URL` too). |
| `frontend/index.html` | The entire UI: structure, styles, and behavior in one file. No build step — edit and refresh. |
| `frontend/config.js` | The one piece of environment-specific frontend config (`window.API_BASE`). Kept separate from `index.html` so it's a one-line edit per environment instead of hunting through the main file. |
| `docker-compose.yml` | Local all-in-one dev environment: backend container + nginx serving `frontend/` statically. Not used in production deployment. |
| `render.yaml` | Optional alternate backend deployment path (Render). Kept as documented fallback since Render requires a card for free-tier verification and Vercel doesn't. |
| `netlify.toml` | Tells Netlify to publish `frontend/` with no build command. |
| `README.md` | Quick-start, API reference, deployment steps — the "how to run/deploy this" doc. |
| `GUIDE.md` | This file — the "how it works and why" doc. |

---

## 5. Local development

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in GEMINI_API_KEY (free, no card: aistudio.google.com/apikey)
uvicorn main:app --reload     # http://localhost:8000

# Frontend (separate terminal)
cd frontend
python3 -m http.server 3000   # http://localhost:3000
```

`config.js` auto-detects `localhost`/`127.0.0.1` and points at `http://localhost:8000`
in that case, so no edits are needed to test locally against a local backend. Set
`ALLOWED_ORIGINS=http://localhost:3000` in `backend/.env` so CORS allows the local
frontend.

Or: `docker-compose up --build` runs both at once (frontend on :3000, backend on :8000).

---

## 6. Deployment

**Live URLs:**
- Frontend: https://merry-biscotti-bc274f.netlify.app
- Backend: https://ai-resume-screener-plum.vercel.app
- Repo: https://github.com/AleenaSehar/ai-resume-screener (public)

**How it's wired:** both Netlify and Vercel are connected directly to the GitHub
repo. Every push to `master` triggers both platforms to rebuild and redeploy
automatically — no manual deploy step, no CI config needed for this.

**Backend → Vercel.** Root Directory set to `backend` in the Vercel project
settings; Vercel auto-detects FastAPI from `requirements.txt`. Env vars:
`GEMINI_API_KEY` (secret), `ALLOWED_ORIGINS` (the Netlify URL, so CORS allows it),
optionally `DATABASE_URL` (Neon pooled connection string, for screening
history — omit it and the app runs identically minus history), and optionally
`JWT_SECRET` (enables accounts; requires `DATABASE_URL` too — generate with
`python -c "import secrets; print(secrets.token_hex(32))"`, at least 32 bytes
or PyJWT warns). Setting up history: create a free Neon project, run
`backend/schema.sql` once in its SQL editor, copy the pooled connection string
into `DATABASE_URL`. Full steps in `README.md`.

**Frontend → Netlify.** `netlify.toml` at the repo root publishes `frontend/`
directly, no build command. `frontend/config.js` hardcodes the production Vercel URL
for any non-localhost hostname.

### Why these two platforms specifically (a short decision log)

The path here wasn't the first choice at every step — worth knowing if revisiting:

- **Backend hosting was tried three ways before landing on Vercel:**
  1. **Render** — rejected; asks for a credit card even for the free tier, which the
     user didn't want to give.
  2. **Hugging Face Spaces (Docker)** — built out fully (root `Dockerfile`, Spaces
     metadata), but HF changed policy to require a paid plan for any Docker/compute
     Space (static-only stays free) — a dead end discovered only after building it.
  3. **Vercel** — works: no card for Hobby, native FastAPI support, Python functions
     get up to 300s (plenty for a Gemini call). This is what's live today.
- **AI provider switched from Anthropic Claude to Google Gemini** because the user
  didn't have an Anthropic API key and wanted a genuinely free alternative. Gemini's
  AI Studio free tier needs no card. This also happened to be a quality-of-life
  upgrade: Gemini's `response_json_schema` support meant dropping a regex-based
  markdown-fence-stripping JSON parser (the original Claude implementation) for a
  schema-enforced one.
- **The original `anthropic==0.34.2` pin was actually broken** — it crashes on
  import with any current `httpx` version (`Client.__init__() got an unexpected
  keyword argument 'proxies'`). Found and fixed during pre-deployment testing, before
  the Gemini switch even happened. Lesson embedded in the code: always boot-test
  pinned dependencies, don't just trust that a `requirements.txt` someone wrote once
  still resolves cleanly.
- **PDF resumes are sent to Gemini as native `Part.from_bytes` documents**, not
  extracted to text first. Simpler (no `pypdf`/text-extraction dependency) and
  arguably higher-quality (Gemini reads layout/formatting directly) — this was a
  deliberate choice, not the default/easy path.
- **2MB PDF cap** is not arbitrary — it's sized so the base64-encoded payload plus
  JSON overhead stays safely under **Vercel's 4.5MB request body limit**. Raising
  this cap without checking that constraint again would break large uploads with an
  opaque 413 before the request even reaches application code.
- **PDF export is client-side (jsPDF)**, not server-generated, to avoid adding a
  backend dependency (e.g. `reportlab`/`weasyprint`) and a round-trip for something
  the browser can do entirely on its own from data it already has.
- **Batch screening processes resumes sequentially from the browser against the
  existing `/screen` endpoint**, not via a new server-side batch endpoint — made
  while Gemini's free tier was visibly overloaded, specifically to avoid adding
  concurrent load. Full reasoning in §8's roadmap entry.
- **Screening history uses Neon (serverless Postgres), not Supabase/PlanetScale/
  etc.** — verified directly (not assumed) against Neon's own pricing/docs pages:
  free tier needs no credit card and is "permanent, not a trial" (0.5GB storage,
  100 compute-hours/month — plenty here), and their docs explicitly cover
  serverless-function usage (Vercel named directly): use the **pooled** connection
  string (`-pooler` hostname suffix, via PgBouncer) instead of the direct one, to
  avoid connection exhaustion from many short-lived function invocations each
  opening a fresh connection. This project has been burned twice already by
  assuming a "free tier" needed no card without checking (Render, then Hugging
  Face Spaces) — Neon's claim was verified live before committing to it, not
  trusted from training data.
- **Python client is `psycopg` v3, not psycopg2, no ORM.** Confirmed installed
  and working (`psycopg.connect()`'s sync API, and that JSONB inserts need
  `psycopg.types.json.Jsonb(dict)` wrapping) before writing the real code — same
  verify-before-shipping convention as every other integration in this project
  (§9). No ORM because there are three simple queries total; SQLAlchemy would be
  the kind of premature abstraction §9 already warns against.
- **`connect_timeout=5` on every database connection is required, not
  decorative.** Without a bound, a connection attempt to an unreachable host can
  hang far longer than a request should ever take, even though the
  `try/except` around it looks like it "handles" the failure — the exception
  only fires once the attempt gives up. This was verified directly: pointing
  `DATABASE_URL` at a non-routable IP and confirming `/screen` still returned a
  full result in ~13s total (Gemini latency + a bounded ~5s DB timeout), not
  hanging indefinitely.
- **History-saving is best-effort; reading history is not.** `POST /screen`
  never fails because of a database problem (there's a real feature underneath
  it to protect). `GET /history` does return a real `502` on a genuine DB error,
  because there's nothing else under it to protect — the whole point of the
  request is the history data. Both behaviors were explicitly tested (unset
  `DATABASE_URL`, unreachable `DATABASE_URL`, and a reachable one), not just
  written and assumed correct.
- **Auth is self-rolled (bcrypt + PyJWT) on the existing stack**, not a
  third-party service (Auth0/Clerk/Supabase Auth/etc.) and not OAuth —
  verified both libraries' real APIs before writing code (`bcrypt.hashpw`/
  `checkpw`, `jwt.encode`/`decode`, PyJWT's own warning about secrets under 32
  bytes) rather than guessing. Deliberate to avoid a new external dependency
  for a small project already running its own backend and database.
- **JWT in `localStorage`, not an httpOnly cookie** — frontend (Netlify) and
  backend (Vercel) are different origins, so cookie auth would need fragile
  cross-site cookie settings (`SameSite=None; Secure`, increasingly squeezed by
  browser third-party-cookie policy). Matches the existing `ANONYMOUS_ID`
  pattern already in this codebase. Accepted trade-off: an XSS bug could
  exfiltrate this token where an httpOnly cookie couldn't be read by JS at
  all — which is exactly why `escapeHtml()` shipped in the same changeset
  rather than as a someday-cleanup (see §3d, point 8).
- **Hybrid authorization rule** (no header = anonymous, bad/expired header =
  hard `401`, everywhere) over the simpler binary options. A real gap in each
  of those is why this got the hybrid treatment: "always require a header"
  would break the no-login requirement; "always ignore invalid headers and
  fall back to anonymous" would mean a user's own screenings silently stop
  attributing to their account with zero indication why, the moment their
  token expires on a tab left open. See §3d, point 4.
- **Login's error message is identical for "wrong password" and "no such
  account"**, with a dummy `bcrypt` comparison run on the no-such-account path
  specifically so the two cases can't be told apart by response timing either
  — not just by the message text. A basic account-enumeration mitigation that
  costs one extra hashed comparison.
- **`screenings.anonymous_id` stays on a claimed row rather than being nulled
  out.** Once `user_id` is set, `anonymous_id` is inert for scoping purposes
  (checked everywhere), but keeping it lets a browser later reused by a
  *different* account still correctly claim only the rows nobody has claimed
  yet (`... AND user_id IS NULL` on every claim and every anonymous-path
  query) — nulling it out would lose that history trail for no benefit.

---

## 7. Known limitations / things to watch

- **Rate limiting isn't a hard global cap.** `slowapi` keeps counters in memory,
  which doesn't persist across Vercel's serverless function instances. Fine for a
  personal project; would need a shared store (Redis, etc.) under real traffic.
- **Anonymous (logged-out) history still relies on `anonymous_id`, which is
  not a security boundary.** It's a client-generated identifier a user could
  inspect, copy, or guess the *presence* of (UUIDs themselves are impractical
  to guess, but *knowing* someone else's — e.g. shared over a support channel
  — is enough to read or delete their anonymous history). This is why accounts
  exist now: signing up moves history to real `user_id` scoping, verified
  against a password — don't rely on the anonymous mode for anything actually
  sensitive.
- **No password-reset flow.** Self-rolled email+password with no transactional
  email provider means a forgotten password **permanently locks that account**,
  including its claimed history — there's no recovery path today. Accepted for
  now; would need an email provider (e.g. Resend) to fix, which is a real new
  external dependency, not a small addition.
- **JWTs are long-lived (30 days) with no refresh mechanism.** Simpler than
  building refresh-token rotation for a tool this size, but it means a
  compromised token stays valid for up to 30 days with no way to revoke it
  short of changing `JWT_SECRET` (which logs out every account at once).
- **History storage is optional and additive, not a hard dependency.** Without
  `DATABASE_URL` set, the app behaves exactly as it did before this feature
  existed — nothing is stored, refresh the page and only the last *rendered*
  analysis is gone (an exported PDF survives, since that's a local download).
  Auth requires `DATABASE_URL` too (no database, no `users` table, no accounts)
  and degrades the same way: unset `JWT_SECRET` → `/auth/*` returns a clean
  `503`, everything else works exactly as if accounts didn't exist.
- **Gemini free tier has occasional `503 UNAVAILABLE` ("model overloaded") errors.**
  This is Google's infrastructure, not a bug here — the app surfaces the real error
  message and the fix is just "try again." Observed directly during testing (two
  back-to-back 503s, then a clean success on retry).
- **Accounts are optional, never required.** Anyone with the frontend URL can
  use the tool fully anonymously, subject to the (soft) rate limit — signing up
  only matters if you want durable, cross-browser history.
- **Vercel Hobby plan constraints apply**: 4.5MB request body (drives the PDF size
  cap), 300s max function duration (not currently a bottleneck).

---

## 8. Roadmap

Source of truth for the checklist itself is `README.md`; this section adds the
*why/how* behind each item.

- [x] **PDF upload support (drag & drop)** — done. Resume can be pasted as text or
  uploaded as a PDF (tab toggle, drag-and-drop zone, 2MB cap). PDF sent natively to
  Gemini, not text-extracted.
- [x] **Export results as PDF report** — done. Client-side via jsPDF, one click,
  no backend involvement.
- [x] **Batch screening (multiple resumes vs one JD)** — done. PDF-only (paste-text
  mode stays single-resume), max 5 files, processed **sequentially from the
  browser** against the existing `/screen` endpoint — no new backend endpoint, no
  backend changes at all. This was a deliberate choice over a server-side batch
  endpoint: Gemini's free tier was visibly overloaded (`503`s) while this was being
  built, and the user explicitly asked to keep load low. A server-side batch
  endpoint processing 5 resumes in one request would also have risked bumping into
  Vercel's function duration at scale, and would have given no partial results
  until the entire batch finished. The sequential client-side loop avoids both
  problems and reuses 100% of existing validation/error-handling. See §3b for the
  full walkthrough.
- [x] **Database storage for screening history** — done. The first feature
  requiring persistent state in this project (Neon/Postgres, `backend/schema.sql`,
  reachable from Vercel's stateless functions via a pooled connection string).
  Scoped per-browser via a client-generated `anonymous_id` (localStorage), not
  real accounts — done this way specifically to avoid pulling in auth (the next
  roadmap item) just to ship history, while staying forward-compatible: the
  anonymous ID can be linked to a real account later instead of thrown away.
  Saving is best-effort and never blocks `/screen`; reading/deleting history
  fails loudly (a real `502`) since there's no core feature underneath them to
  protect the way `/screen` protects itself. See §3c and §6.
- [x] **Auth + user accounts** — done. Self-rolled email+password (bcrypt +
  PyJWT) on the existing Neon database — no new external service, no OAuth.
  Upgrades history from browser-scoped (`anonymous_id`) to real `user_id`
  scoping, closing the "not a real security boundary" gap from the previous
  entry, while staying fully opt-in: signing in is never required, and
  existing anonymous history is automatically claimed onto a new account at
  signup rather than orphaned. Accepted scope cuts, documented in §7: no
  password-reset flow (no email provider), 30-day JWTs with no refresh. Caught
  and fixed two real pre-existing bugs while building this (an anonymous-path
  history-scoping gap on claimed rows, and a CORS config that had silently
  broken the history feature's delete buttons in the real browser-deployed app
  since that feature shipped) — see §3d.
- [ ] **Chrome extension** — not started. Would likely reuse the existing `/screen`
  API as-is; the work is almost entirely a new frontend surface (extension popup +
  content script to pull JD text off a job posting page), not a backend change.
- [ ] **Chrome extension** — not started. Would likely reuse the existing `/screen`
  API as-is; the work is almost entirely a new frontend surface (extension popup +
  content script to pull JD text off a job posting page), not a backend change.

---

## 9. Conventions worth knowing

- **Backend is one file on purpose.** It's small enough that splitting into modules
  (routes/services/schemas) would be premature structure. Revisit if it grows past
  a few hundred lines or gains a second real endpoint.
- **Frontend has no build step on purpose.** Editing `index.html` directly and
  refreshing is the whole dev loop. If this ever needs a component framework or
  TypeScript, that's a real architectural shift, not a small addition — call it out
  explicitly before doing it.
- **Every third-party API integration in this project has been version-verified
  against the real installed package/SDK before shipping**, not written from
  memory/training data alone — both the `anthropic`→`google-genai` switch and the
  jsPDF integration involved fetching current docs and/or inspecting the installed
  package to confirm method signatures, because API shapes drift and guessing wrong
  wastes a debugging cycle. Keep doing this for future integrations.
- **The network-calling function is a deliberately mockable seam.** `screenRequest()`
  is declared as a top-level `function` (not `const`/arrow) in the main, non-module
  `<script>` tag, which means it's reachable as `window.screenRequest`. Tests (run
  via Playwright + a real headless Chrome, no framework installed in this repo —
  see below) monkey-patch it to return canned results instead of depending on live
  Gemini calls, which is what let the batch-mode loop, retry, and ranking logic get
  verified even while the live API was actively unreliable. Keep new
  network-calling logic behind a similarly reachable function if it needs the same
  kind of testing.
- **No test framework is installed.** Verification for UI changes in this project
  has consistently meant: serve `frontend/` with `python3 -m http.server`, drive it
  with `playwright-core` against the system's `/usr/bin/google-chrome`
  (`chromium.launch({ executablePath: '/usr/bin/google-chrome' })`) from a
  throwaway script in a scratch directory (never committed), and check real
  rendered output — screenshots, extracted PDF text via `pdftotext`/`pypdf`, DOM
  state via `page.evaluate()`. This has caught real bugs before they shipped (a
  `[hidden]` CSS-specificity bug, a sticky-nav scroll-hiding regression, an
  expanded-row-collapses-on-retry UX gap, a database scoping gap on claimed
  history rows) — keep testing changes this way rather than trusting the code
  by inspection alone.
- **CORS behavior specifically needs a real-browser check, not just `curl`.**
  The history feature's `DELETE` endpoints shipped fully verified by `curl` and
  local backend tests and were still silently broken in the actual deployed
  app for a full feature cycle, because neither test method goes through
  browser CORS preflight enforcement at all — only a real cross-origin
  `fetch()` does. Caught only when auth work required re-checking
  `allow_methods`/`allow_headers`. Any future endpoint addition (new HTTP
  method, new required header) needs either a real-browser
  cross-origin check or, at minimum, an explicit manual review of
  `CORSMiddleware`'s `allow_methods`/`allow_headers` against what's actually
  being added — don't assume existing CORS config covers a new endpoint shape.
- **A function declared as a top-level `function` (not `const`/`let`) in this
  project's single non-module `<script>` tag is reachable as `window.fnName`,
  and is the deliberate pattern for anything that needs to be mockable in a
  Playwright test** (`screenRequest`, `loadHistoryRequest`,
  `signupRequest`/`loginRequest`, etc.) — reassigning `window.fnName` in a test
  overrides every call site that references the bare identifier, since they
  all resolve against the same global binding. The one place this *doesn't*
  work: code that runs synchronously at page-load time (e.g. `restoreSession()`
  calling `authFetch()` on load) executes *before* any `page.evaluate()`
  injected after `page.goto()`/`page.reload()` can set up a mock — for that,
  use Playwright's `page.route()` network interception instead (set up
  *before* navigation), not a function override.
