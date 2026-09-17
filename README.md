# AI Resume Screener

An AI-powered resume screening tool built with **FastAPI** + **Google Gemini API** + vanilla HTML/CSS/JS.

Paste a job description and a resume (as text or a PDF upload) → get an instant match score, skill breakdown, strengths, gaps, and actionable feedback.

> New to this project? [GUIDE.md](GUIDE.md) walks through the architecture, how each
> piece works, the deployment setup, and the roadmap in more depth than this README.

---

## Features

- **Resume input as text or PDF** — paste it in, or drag-and-drop/upload a PDF (max 2MB)
- **Match score** (0–100) with color-coded verdict
- **Category breakdown** — skills, experience, education scores
- **Skill tags** — matched, missing, and bonus skills
- **Strengths & gaps** analysis
- **Actionable suggestions** to improve resume fit
- **Plain-English summary** of the analysis
- **Export the report as a PDF** — one click, generated client-side
- **Batch screening** — upload up to 5 resume PDFs at once, get a ranked results table, export one combined PDF report
- Clean dark UI, fully responsive

---

## Tech Stack

| Layer     | Tech                          |
|-----------|-------------------------------|
| Frontend  | HTML, CSS, Vanilla JS, jsPDF  |
| Backend   | Python, FastAPI               |
| AI        | Google Gemini API             |
| Infra     | Docker, Docker Compose        |

---

## Project Structure

```
ai-resume-screener/
├── backend/
│   ├── main.py              # FastAPI app + Gemini integration
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── index.html           # Full UI
│   └── config.js            # API base URL (per-environment)
├── docker-compose.yml
├── render.yaml              # Render backend blueprint
├── netlify.toml             # Netlify frontend config
├── .gitignore
├── README.md
└── GUIDE.md                 # Architecture, roadmap, and step-by-step walkthrough
```

---

## Getting Started

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/ai-resume-screener.git
cd ai-resume-screener
```

### 2. Set up environment

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and add your Gemini API key
```

### 3a. Run with Docker (recommended)

```bash
docker-compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

### 3b. Run manually

**Backend:**
```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

**Frontend:**
```bash
# Just open frontend/index.html in your browser
# OR serve with any static server:
cd frontend
npx serve .
```

---

## Deployment

The frontend and backend deploy separately.

### Backend → Vercel (no credit card required)

Vercel's Python runtime auto-detects FastAPI from `backend/requirements.txt` and
`backend/main.py` — no Dockerfile or extra config needed.

1. Create a free Vercel account (GitHub sign-in works, no card needed) at
   [vercel.com/new](https://vercel.com/new) and import the `ai-resume-screener` repo.
2. In the import screen, set **Root Directory** to `backend`. Leave the framework
   preset on auto-detect.
3. Under **Environment Variables**, add:
   - `GEMINI_API_KEY` — get one free, no card required, at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
   - `ALLOWED_ORIGINS` — your Netlify site URL once you have it (comma-separated if more than one)
4. Deploy. The resulting URL will be `https://<project-name>.vercel.app`.

> Note: the rate limiter (`slowapi`) keeps its counters in memory, which doesn't
> persist across Vercel's serverless function instances — it still works within a
> single warm instance but isn't a hard global cap in this deployment. Fine for a
> personal project; revisit if this gets real traffic.

An alternative `render.yaml` is also included if you'd rather use Render (requires a card on file for free-tier verification).

### Frontend → Netlify

1. In `frontend/config.js`, replace `REPLACE_WITH_YOUR_RENDER_URL` with your Vercel URL from above.
2. In Netlify, create a **New site from Git**, pointing at this repo (it will pick up `netlify.toml`, which publishes the `frontend/` folder — no build step needed).
3. Deploy. Take the resulting Netlify URL and set it as `ALLOWED_ORIGINS` on the Vercel project (step 3 above), then redeploy so CORS allows it.

---

## API Reference

### `POST /screen`

Analyze how well a resume matches a job description. Provide the resume as
plain text **or** as a base64-encoded PDF — not both.

**Request body (text resume):**
```json
{
  "job_description": "string",
  "resume": "string"
}
```

**Request body (PDF resume):**
```json
{
  "job_description": "string",
  "resume_file": "base64-encoded PDF data, no data: URL prefix",
  "resume_file_name": "optional original filename, e.g. resume.pdf"
}
```

`resume_file` is capped at 2MB decoded (kept well under Vercel's 4.5MB request
body limit once base64-encoded). Gemini reads the PDF directly — there's no
separate text-extraction step.

**Response:**
```json
{
  "score": 78,
  "verdict": "Strong match with a few skill gaps to address",
  "verdict_detail": "...",
  "skills_score": 82,
  "experience_score": 75,
  "education_score": 90,
  "matched_skills": ["Python", "FastAPI", "PostgreSQL"],
  "missing_skills": ["Kubernetes", "Kafka"],
  "bonus_skills": ["Redis", "Docker"],
  "strengths": ["..."],
  "gaps": ["..."],
  "suggestions": ["..."],
  "summary": "..."
}
```

---

## Environment Variables

| Variable             | Description                                          |
|----------------------|-------------------------------------------------------|
| `GEMINI_API_KEY`     | Your Google Gemini API key                              |
| `ALLOWED_ORIGINS`    | Comma-separated list of origins allowed to call the API |

Get a free key (no credit card required) at: https://aistudio.google.com/apikey

---

## Roadmap

- [x] PDF upload support (drag & drop)
- [x] Export results as PDF report
- [x] Batch screening (multiple resumes vs one JD)
- [ ] Database storage for screening history
- [ ] Auth + user accounts
- [ ] Chrome extension

---

## License

MIT — feel free to use, fork, and build on this.

---

Built by [Your Name](https://github.com/YOUR_USERNAME) · Powered by [Google Gemini](https://ai.google.dev)
