import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
import json

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

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

MAX_INPUT_LENGTH = 20000
MODEL = "gemini-3.5-flash"


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
with precision and fairness."""

ANALYSIS_PROMPT = """Analyze how well this resume matches the job description.

JOB DESCRIPTION:
{jd}

RESUME:
{resume}

Score the match (0-100 overall, plus skills/experience/education sub-scores), identify matched,
missing, and bonus skills, list concrete strengths and gaps, give actionable suggestions to
improve fit, and write a short plain-English summary."""

RESPONSE_SCHEMA = ScreenResult.model_json_schema()


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
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_json_schema=RESPONSE_SCHEMA,
            ),
        )
        result = json.loads(response.text)
        return ScreenResult(**result)

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse AI response: {str(e)}")
    except genai_errors.APIError as e:
        raise HTTPException(status_code=502, detail=f"AI API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
