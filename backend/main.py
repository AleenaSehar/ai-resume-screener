import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import anthropic
import json
import re

app = FastAPI(title="AI Resume Screener API", version="1.0.0")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

client = anthropic.Anthropic()

MAX_INPUT_LENGTH = 20000
MODEL = "claude-sonnet-5"


class ScreenRequest(BaseModel):
    job_description: str
    resume: str


class ScreenResult(BaseModel):
    score: int
    verdict: str
    verdict_detail: str
    skills_score: int
    experience_score: int
    education_score: int
    matched_skills: list[str]
    missing_skills: list[str]
    bonus_skills: list[str]
    strengths: list[str]
    gaps: list[str]
    suggestions: list[str]
    summary: str


SYSTEM_PROMPT = """You are an expert technical recruiter with 15+ years of experience screening 
candidates for software engineering and tech roles. You analyze resumes against job descriptions 
with precision and fairness. Always return valid JSON only."""

ANALYSIS_PROMPT = """Analyze how well this resume matches the job description.

JOB DESCRIPTION:
{jd}

RESUME:
{resume}

Return ONLY a valid JSON object with no markdown, no explanation, no backticks:
{{
  "score": <integer 0-100, overall match score>,
  "verdict": "<one strong sentence about overall fit, 10-15 words>",
  "verdict_detail": "<2-3 sentence nuanced explanation>",
  "skills_score": <integer 0-100>,
  "experience_score": <integer 0-100>,
  "education_score": <integer 0-100>,
  "matched_skills": ["skill1", "skill2"],
  "missing_skills": ["skill1", "skill2"],
  "bonus_skills": ["skill1", "skill2"],
  "strengths": ["specific strength 1", "specific strength 2", "specific strength 3"],
  "gaps": ["specific gap 1", "specific gap 2"],
  "suggestions": ["actionable suggestion 1", "actionable suggestion 2", "actionable suggestion 3"],
  "summary": "<3-4 sentences comprehensive analysis summary>"
}}"""


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```json|```", "", text).strip()
    return json.loads(text)


@app.get("/")
def root():
    return {"status": "ok", "message": "AI Resume Screener API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/screen", response_model=ScreenResult)
@limiter.limit("10/minute")
def screen_resume(req: ScreenRequest, request: Request):
    jd = req.job_description.strip()
    resume = req.resume.strip()

    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job description too short")
    if len(resume) < 50:
        raise HTTPException(status_code=400, detail="Resume too short")
    if len(jd) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail="Job description too long")
    if len(resume) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail="Resume too long")

    prompt = ANALYSIS_PROMPT.format(jd=jd, resume=resume)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        result = extract_json(raw)
        return ScreenResult(**result)

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse AI response: {str(e)}")
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"AI API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
