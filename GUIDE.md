# Project Guide — AI Resume Screener

> **Maintenance note:** This file is meant to always reflect the current state of the
> project — architecture, roadmap, and decisions. Any time the codebase changes in a
> way that affects what's described here, this file gets updated in the same
> changeset. If you're reading this and something looks out of sync with the code,
> that's a bug in this doc — flag it.

Last updated: 2026-09-17 (batch screening)

---

## 1. What this is

A single-purpose web tool: paste a job description and a resume (as text or a PDF
upload), and get back an AI-generated match analysis — a 0–100 score, a skills/
experience/education breakdown, matched/missing/bonus skills, strengths, gaps,
actionable suggestions, and a plain-English summary. Results can be exported as a
PDF report. Up to 5 resume PDFs can be screened against one job description at once
in **batch mode**, producing a ranked comparison table and a combined PDF report.

It's intentionally small: no database, no auth, no build step on the frontend. Two
deployable pieces (a static frontend and a stateless API), one external dependency
(Google Gemini).

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
- **Backend**: one FastAPI app (`backend/main.py`), a single real endpoint
  (`POST /screen`). Deployed to **Vercel** as a Python serverless function — Vercel
  auto-detects FastAPI from `requirements.txt` and runs `main.py` directly, no
  Dockerfile involved on that path.
- **AI**: Google Gemini (`gemini-3.5-flash`), called via the `google-genai` SDK.
  Gemini's structured-output mode (`response_json_schema`, built directly from the
  `ScreenResult` Pydantic model) guarantees the response matches the shape the
  frontend expects — no fragile "strip markdown fences and hope it's valid JSON"
  parsing.
- **No database, no auth, no server-side session state.** Every request is
  independent; nothing persists between analyses.

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

---

## 4. File-by-file reference

| File | Purpose |
|---|---|
| `backend/main.py` | The entire API: CORS, rate limiting, request validation, the Gemini call, error handling. One file by design — small enough not to need more structure yet. |
| `backend/requirements.txt` | Pinned dependencies. Versions matter here — see §7, the `anthropic`/`httpx` incompatibility bug is a cautionary example of why. |
| `backend/Dockerfile` | Used by `docker-compose.yml` (local dev) and by Render if you deploy there via `render.yaml`. **Not** used by the Vercel deployment path — Vercel runs `main.py` directly via its Python runtime. |
| `backend/.env.example` | Template for local secrets. Copy to `backend/.env` (gitignored) and fill in `GEMINI_API_KEY`. |
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
`GEMINI_API_KEY` (secret) and `ALLOWED_ORIGINS` (the Netlify URL, so CORS allows it).

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

---

## 7. Known limitations / things to watch

- **Rate limiting isn't a hard global cap.** `slowapi` keeps counters in memory,
  which doesn't persist across Vercel's serverless function instances. Fine for a
  personal project; would need a shared store (Redis, etc.) under real traffic.
- **No persistence.** Nothing is stored — refresh the page and the last analysis is
  gone (except an exported PDF you downloaded). This is intentional for now; see the
  roadmap.
- **Gemini free tier has occasional `503 UNAVAILABLE` ("model overloaded") errors.**
  This is Google's infrastructure, not a bug here — the app surfaces the real error
  message and the fix is just "try again." Observed directly during testing (two
  back-to-back 503s, then a clean success on retry).
- **No auth, no per-user anything.** Anyone with the frontend URL can use it, subject
  to the (soft) rate limit.
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
- [ ] **Database storage for screening history** — not started. Would be the first
  feature to actually require a database and probably a rethink of "no server-side
  state" as an architectural property. Needs a persistence layer (Postgres/SQLite/etc.)
  reachable from the Vercel serverless functions — likely a hosted DB (e.g. Neon,
  Supabase) since Vercel functions are stateless/ephemeral.
- [ ] **Auth + user accounts** — not started. Would gate history storage per-user.
  Bigger scope than it sounds: session/token handling, at minimum.
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
  expanded-row-collapses-on-retry UX gap) — keep testing changes this way rather
  than trusting the code by inspection alone.
