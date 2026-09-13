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

### Backend → Render

1. Push this repo to GitHub.
2. In Render, create a **New Blueprint** and point it at this repo (it will pick up `render.yaml`).
3. Set the `ANTHROPIC_API_KEY` and `ALLOWED_ORIGINS` environment variables in the Render dashboard.
   `ALLOWED_ORIGINS` should be your Netlify site URL (e.g. `https://your-site.netlify.app`), comma-separated if you need more than one.
4. Deploy. Note the resulting `https://<service>.onrender.com` URL.

### Frontend → Netlify

1. In `frontend/config.js`, replace `REPLACE_WITH_YOUR_RENDER_URL` with your Render service's hostname.
2. In Netlify, create a **New site from Git**, pointing at this repo (it will pick up `netlify.toml`, which publishes the `frontend/` folder — no build step needed).
3. Deploy. Take the resulting Netlify URL and set it as `ALLOWED_ORIGINS` on the Render backend (step 3 above), then redeploy the backend so CORS allows it.

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
