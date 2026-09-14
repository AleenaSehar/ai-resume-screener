---
title: AI Resume Screener API
emoji: 🧠
colorFrom: yellow
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# AI Resume Screener

An AI-powered resume screening tool built with **FastAPI** + **Claude API** + vanilla HTML/CSS/JS.

Paste a job description and a resume → get an instant match score, skill breakdown, strengths, gaps, and actionable feedback.

---

## Features

- **Match score** (0–100) with color-coded verdict
- **Category breakdown** — skills, experience, education scores
- **Skill tags** — matched, missing, and bonus skills
- **Strengths & gaps** analysis
- **Actionable suggestions** to improve resume fit
- **Plain-English summary** of the analysis
- Clean dark UI, fully responsive

---

## Tech Stack

| Layer     | Tech                        |
|-----------|-----------------------------|
| Frontend  | HTML, CSS, Vanilla JS       |
| Backend   | Python, FastAPI             |
| AI        | Anthropic Claude API        |
| Infra     | Docker, Docker Compose      |

---

## Project Structure

```
ai-resume-screener/
├── backend/
│   ├── main.py              # FastAPI app + Claude integration
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
└── README.md
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
# Edit backend/.env and add your Anthropic API key
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

### Backend → Hugging Face Spaces (no credit card required)

The repo has a root-level `Dockerfile` and Spaces metadata in this README's YAML
frontmatter specifically for this.

1. Create a free Hugging Face account, then go to [huggingface.co/new-space](https://huggingface.co/new-space).
   Pick the **Docker** SDK, choose a Space name, and create it.
2. Push this repo's content to the new Space's git remote:
   ```bash
   git remote add hf https://huggingface.co/spaces/<your-username>/<space-name>
   git push hf master:main
   ```
   (Space repos default to `main`; this repo's default branch is `master`.)
3. In the Space's **Settings → Variables and secrets**, add:
   - `ANTHROPIC_API_KEY` as a **Secret**
   - `ALLOWED_ORIGINS` as a **Variable**, set to your Netlify site URL once you have it (comma-separated if more than one)
4. The Space will build and start automatically. Its URL will be `https://<your-username>-<space-name>.hf.space`.

An alternative `render.yaml` is also included if you'd rather use Render (requires a card on file for free-tier verification).

### Frontend → Netlify

1. In `frontend/config.js`, replace `REPLACE_WITH_YOUR_RENDER_URL` with your Hugging Face Space URL from above.
2. In Netlify, create a **New site from Git**, pointing at this repo (it will pick up `netlify.toml`, which publishes the `frontend/` folder — no build step needed).
3. Deploy. Take the resulting Netlify URL and set it as `ALLOWED_ORIGINS` in the Space's Settings (step 3 above) — the Space restarts automatically when variables change.

---

## API Reference

### `POST /screen`

Analyze how well a resume matches a job description.

**Request body:**
```json
{
  "job_description": "string",
  "resume": "string"
}
```

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
| `ANTHROPIC_API_KEY`  | Your Anthropic API key                                 |
| `ALLOWED_ORIGINS`    | Comma-separated list of origins allowed to call the API |

Get your key at: https://console.anthropic.com

---

## Roadmap

- [ ] PDF upload support (drag & drop)
- [ ] Export results as PDF report
- [ ] Batch screening (multiple resumes vs one JD)
- [ ] Database storage for screening history
- [ ] Auth + user accounts
- [ ] Chrome extension

---

## License

MIT — feel free to use, fork, and build on this.

---

Built by [Your Name](https://github.com/YOUR_USERNAME) · Powered by [Anthropic Claude](https://anthropic.com)
