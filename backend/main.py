import base64
import binascii
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
MAX_PDF_BYTES = 2 * 1024 * 1024  # keep base64 request body well under Vercel's 4.5MB limit
MODEL = "gemini-3.5-flash"


class ScreenRequest(BaseModel):
    job_description: str
    resume: str | None = None
    resume_file: str | None = None  # base64-encoded PDF (no data: URL prefix)
    resume_file_name: str | None = None


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

ANALYSIS_PROMPT_PDF = """Analyze how well the attached resume (PDF) matches this job description.

JOB DESCRIPTION:
{jd}

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

    if len(jd) < 50:
        raise HTTPException(status_code=400, detail="Job description too short")
    if len(jd) > MAX_INPUT_LENGTH:
        raise HTTPException(status_code=400, detail="Job description too long")

    has_text_resume = bool(req.resume and req.resume.strip())
    has_pdf_resume = bool(req.resume_file)

    if has_text_resume and has_pdf_resume:
        raise HTTPException(status_code=400, detail="Provide either resume text or a resume PDF, not both")
    if not has_text_resume and not has_pdf_resume:
        raise HTTPException(status_code=400, detail="Resume text or a resume PDF is required")

    if has_pdf_resume:
        try:
            pdf_bytes = base64.b64decode(req.resume_file, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Invalid PDF file data")
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise HTTPException(status_code=400, detail="Resume PDF too large (max 2MB)")
        contents = [
            ANALYSIS_PROMPT_PDF.format(jd=jd),
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
        ]
    else:
        resume = req.resume.strip()
        if len(resume) < 50:
            raise HTTPException(status_code=400, detail="Resume too short")
        if len(resume) > MAX_INPUT_LENGTH:
            raise HTTPException(status_code=400, detail="Resume too long")
        contents = ANALYSIS_PROMPT.format(jd=jd, resume=resume)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
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
