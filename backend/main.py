import base64
import binascii
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
import bcrypt
import jwt
import psycopg
from psycopg.types.json import Jsonb
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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

DATABASE_URL = os.environ.get("DATABASE_URL")
logger = logging.getLogger(__name__)

JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 60 * 60 * 24 * 30  # 30 days, no refresh mechanism - see GUIDE.md §7
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72  # bcrypt's own limit; enforce ourselves for a clean 400 instead of a library error

MAX_INPUT_LENGTH = 20000
MAX_PDF_BYTES = 2 * 1024 * 1024  # keep base64 request body well under Vercel's 4.5MB limit
MODEL = "gemini-3.5-flash"


class ScreenRequest(BaseModel):
    job_description: str
    resume: str | None = None
    resume_file: str | None = None  # base64-encoded PDF (no data: URL prefix)
    resume_file_name: str | None = None
    anonymous_id: str | None = None


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


class HistoryEntry(BaseModel):
    id: int
    created_at: datetime
    job_description: str
    resume_text: str | None
    resume_file_name: str | None
    result: ScreenResult


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    anonymous_id: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserInfo(BaseModel):
    id: int
    email: str


class AuthResponse(BaseModel):
    token: str
    user: UserInfo


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


def save_screening(user_id, anonymous_id, job_description, resume_text, resume_file_name, result: ScreenResult):
    if not DATABASE_URL or (not user_id and not anonymous_id):
        return
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO screenings (user_id, anonymous_id, job_description, resume_text, resume_file_name, result)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, anonymous_id, job_description, resume_text, resume_file_name, Jsonb(result.model_dump())),
                )
    except Exception:
        logger.exception("save_screening failed (non-fatal)")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing-safety")


def validate_password(password: str):
    if len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Password is too long")


def create_access_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(request: Request) -> dict | None:
    """No Authorization header -> None (fully anonymous, never an error).
    Header present but invalid/expired -> hard 401, everywhere - gives the
    frontend one consistent 'session died' signal instead of silently
    degrading to anonymous (which would otherwise mean a user's screenings
    quietly stop saving to their account with no indication why)."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    if not auth_header.startswith("Bearer ") or not JWT_SECRET:
        raise HTTPException(status_code=401, detail="Session expired")
    token = auth_header[len("Bearer "):].strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Session expired")


def get_current_user_id(request: Request) -> int | None:
    payload = decode_token(request)
    return int(payload["sub"]) if payload else None


@app.get("/")
def root():
    return {"status": "ok", "message": "AI Resume Screener API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/auth/signup", response_model=AuthResponse)
@limiter.limit("5/minute")
def signup(req: SignupRequest, request: Request):
    if not JWT_SECRET or not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    email = req.email.lower().strip()
    validate_password(req.password)
    password_hash = hash_password(req.password)
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                        (email, password_hash),
                    )
                except psycopg.errors.UniqueViolation:
                    raise HTTPException(status_code=409, detail="An account with this email already exists")
                user_id = cur.fetchone()[0]
                if req.anonymous_id:
                    cur.execute(
                        "UPDATE screenings SET user_id = %s WHERE anonymous_id = %s AND user_id IS NULL",
                        (user_id, req.anonymous_id),
                    )
    except HTTPException:
        raise
    except Exception:
        logger.exception("signup failed")
        raise HTTPException(status_code=500, detail="Could not create account")
    token = create_access_token(user_id, email)
    return AuthResponse(token=token, user=UserInfo(id=user_id, email=email))


@app.post("/auth/login", response_model=AuthResponse)
@limiter.limit("5/minute")
def login(req: LoginRequest, request: Request):
    if not JWT_SECRET or not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    email = req.email.lower().strip()
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password_hash FROM users WHERE email = %s", (email,))
                row = cur.fetchone()
    except Exception:
        logger.exception("login failed")
        raise HTTPException(status_code=500, detail="Could not log in right now")
    if row:
        user_id, password_hash = row
        password_ok = verify_password(req.password, password_hash)
    else:
        verify_password(req.password, _DUMMY_HASH)  # keeps response time ~constant either way
        password_ok = False
    if not row or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user_id, email)
    return AuthResponse(token=token, user=UserInfo(id=user_id, email=email))


@app.get("/auth/me", response_model=UserInfo)
def get_me(request: Request):
    if not JWT_SECRET:
        raise HTTPException(status_code=503, detail="Auth is not configured")
    payload = decode_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return UserInfo(id=int(payload["sub"]), email=payload["email"])


@app.post("/screen", response_model=ScreenResult)
@limiter.limit("10/minute")
def screen_resume(req: ScreenRequest, request: Request):
    user_id = get_current_user_id(request)
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

    resume_text_for_storage = None

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
        resume_text_for_storage = resume
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
        screen_result = ScreenResult(**result)
        save_screening(
            user_id, req.anonymous_id, jd, resume_text_for_storage,
            req.resume_file_name if has_pdf_resume else None, screen_result,
        )
        return screen_result

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse AI response: {str(e)}")
    except genai_errors.APIError as e:
        raise HTTPException(status_code=502, detail=f"AI API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@app.get("/history", response_model=list[HistoryEntry])
@limiter.limit("30/minute")
def get_history(request: Request, anonymous_id: str | None = None, limit: int = 20):
    limit = max(1, min(limit, 50))
    user_id = get_current_user_id(request)
    if user_id is None and not anonymous_id:
        raise HTTPException(status_code=400, detail="anonymous_id is required when not logged in")
    if not DATABASE_URL:
        return []
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                if user_id is not None:
                    cur.execute(
                        """
                        SELECT id, created_at, job_description, resume_text, resume_file_name, result
                        FROM screenings WHERE user_id = %s
                        ORDER BY created_at DESC LIMIT %s
                        """,
                        (user_id, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, created_at, job_description, resume_text, resume_file_name, result
                        FROM screenings WHERE anonymous_id = %s AND user_id IS NULL
                        ORDER BY created_at DESC LIMIT %s
                        """,
                        (anonymous_id, limit),
                    )
                rows = cur.fetchall()
    except Exception:
        logger.exception("get_history failed")
        raise HTTPException(status_code=502, detail="Could not load history right now")
    return [
        HistoryEntry(id=r[0], created_at=r[1], job_description=r[2], resume_text=r[3],
                     resume_file_name=r[4], result=r[5])
        for r in rows
    ]


@app.delete("/history/{screening_id}")
@limiter.limit("20/minute")
def delete_history_entry(screening_id: int, request: Request, anonymous_id: str | None = None):
    user_id = get_current_user_id(request)
    if user_id is None and not anonymous_id:
        raise HTTPException(status_code=400, detail="anonymous_id is required when not logged in")
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="History storage is not configured")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                if user_id is not None:
                    cur.execute(
                        "DELETE FROM screenings WHERE id = %s AND user_id = %s",
                        (screening_id, user_id),
                    )
                else:
                    cur.execute(
                        "DELETE FROM screenings WHERE id = %s AND anonymous_id = %s AND user_id IS NULL",
                        (screening_id, anonymous_id),
                    )
                deleted = cur.rowcount
    except Exception:
        logger.exception("delete_history_entry failed")
        raise HTTPException(status_code=502, detail="Could not delete this entry right now")
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "deleted"}


@app.delete("/history")
@limiter.limit("10/minute")
def delete_all_history(request: Request, anonymous_id: str | None = None):
    user_id = get_current_user_id(request)
    if user_id is None and not anonymous_id:
        raise HTTPException(status_code=400, detail="anonymous_id is required when not logged in")
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="History storage is not configured")
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                if user_id is not None:
                    cur.execute("DELETE FROM screenings WHERE user_id = %s", (user_id,))
                else:
                    cur.execute("DELETE FROM screenings WHERE anonymous_id = %s AND user_id IS NULL", (anonymous_id,))
                deleted = cur.rowcount
    except Exception:
        logger.exception("delete_all_history failed")
        raise HTTPException(status_code=502, detail="Could not clear history right now")
    return {"status": "deleted", "count": deleted}
