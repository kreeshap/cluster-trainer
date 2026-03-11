import os
import asyncio
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
from typing import Optional
import pathlib

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_ANON_KEY")
service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", key)

supabase: Client = create_client(url, key)
supabase_admin: Client = create_client(url, service_key)

app = FastAPI(title="Cluster Trainer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
_base         = pathlib.Path(__file__).parent
_frontend_dir = os.environ.get("FRONTEND_DIR", "")
_frontend     = _base / _frontend_dir if _frontend_dir else _base

app.mount("/app", StaticFiles(directory=str(_frontend), html=True), name="frontend")


# ── In-memory job tracker for /generate/all ──────────────────
_bulk_job: dict = {"running": False, "progress": 0, "total": 0, "failed": 0, "done": False, "error": None}


class SignUpRequest(BaseModel):
    email: str
    password: str

class SignInRequest(BaseModel):
    email: str
    password: str

class QuizAttemptRequest(BaseModel):
    question_id: str
    selected_answer: str
    time_taken: Optional[int] = None

class GenerateRequest(BaseModel):
    kpi_code: str
    question_type: str
    difficulty: str
    count: int = 1

class GenerateAllRequest(BaseModel):
    kpi_codes: Optional[list[str]] = None   # None = all KPIs
    questions_per_kpi: int = 20


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        user = supabase.auth.get_user(token)
        return user.user
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")



@app.post("/auth/signup")
async def sign_up(body: SignUpRequest):
    try:
        response = supabase.auth.sign_up({"email": body.email, "password": body.password})
        return {"message": "Check your email to confirm your account.", "user_id": response.user.id if response.user else None}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/signin")
async def sign_in(body: SignInRequest):
    try:
        response = supabase.auth.sign_in_with_password({"email": body.email, "password": body.password})
        return {
            "access_token": response.session.access_token,
            "token_type": "bearer",
            "user": {"id": response.user.id, "email": response.user.email}
        }
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid email or password")


@app.post("/auth/signout")
async def sign_out(user=Depends(get_current_user)):
    supabase.auth.sign_out()
    return {"message": "Signed out successfully"}



@app.get("/clusters")
async def get_clusters():
    return supabase.table("clusters").select("*").execute().data

@app.get("/clusters/{cluster_id}/kpis")
async def get_kpis_by_cluster(cluster_id: str):
    return supabase.table("kpis").select("*").eq("cluster_id", cluster_id).execute().data

@app.get("/kpis")
async def get_all_kpis():
    return supabase.table("kpis").select("*, clusters(name)").execute().data

@app.get("/kpis/{kpi_code}")
async def get_kpi(kpi_code: str):
    response = supabase.table("kpis").select("*").eq("kpi_code", kpi_code).single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="KPI not found")
    return response.data



@app.get("/questions")
async def get_questions(
    cluster: Optional[str] = None,
    kpi_code: Optional[str] = None,
    difficulty: Optional[str] = None,
    question_type: Optional[str] = None,
    limit: int = 20
):
    query = supabase.table("questions").select("*")
    if cluster:       query = query.eq("cluster", cluster)
    if kpi_code:      query = query.eq("kpi_code", kpi_code)
    if difficulty:    query = query.eq("difficulty", difficulty)
    if question_type: query = query.eq("question_type", question_type)
    return query.limit(limit).execute().data

@app.get("/questions/{question_id}")
async def get_question(question_id: str):
    response = (
        supabase.table("questions")
        .select("id, scenario, question, answer_a, answer_b, answer_c, answer_d, kpi_code, cluster, question_type, difficulty")
        .eq("id", question_id).single().execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Question not found")
    return response.data

@app.post("/quiz/attempt")
async def submit_attempt(body: QuizAttemptRequest, user=Depends(get_current_user)):
    q = supabase.table("questions").select("correct, explanation, kpi_code").eq("id", body.question_id).single().execute()
    if not q.data:
        raise HTTPException(status_code=404, detail="Question not found")
    is_correct = body.selected_answer.upper() == q.data["correct"].upper()
    supabase.table("quiz_history").insert({
        "user_id": user.id, "question_id": body.question_id,
        "kpi_code": q.data["kpi_code"], "selected_answer": body.selected_answer.upper(),
        "is_correct": is_correct, "time_taken": body.time_taken,
    }).execute()
    return {"is_correct": is_correct, "correct_answer": q.data["correct"], "explanation": q.data["explanation"]}

@app.get("/quiz/history")
async def get_quiz_history(user=Depends(get_current_user), limit: int = 50):
    return (
        supabase.table("quiz_history")
        .select("*, questions(question, kpi_code, difficulty)")
        .eq("user_id", user.id).order("created_at", desc=True).limit(limit).execute().data
    )

@app.get("/quiz/stats")
async def get_user_stats(user=Depends(get_current_user)):
    response = supabase.table("quiz_history").select("kpi_code, is_correct").eq("user_id", user.id).execute()
    stats: dict = {}
    for row in response.data:
        kpi = row["kpi_code"]
        if kpi not in stats:
            stats[kpi] = {"total": 0, "correct": 0}
        stats[kpi]["total"] += 1
        if row["is_correct"]:
            stats[kpi]["correct"] += 1
    return [
        {"kpi_code": kpi, "total": v["total"], "correct": v["correct"],
         "accuracy": round(v["correct"] / v["total"] * 100, 1) if v["total"] else 0}
        for kpi, v in stats.items()
    ]



@app.post("/generate")
async def generate_questions_route(body: GenerateRequest, user=Depends(get_current_user)):
    from generator import generate_question, check_answer_balance
    results = []
    for _ in range(body.count):
        balance = check_answer_balance(body.kpi_code.split(":")[0])
        force   = balance["suggest"] if not balance["balanced"] else None
        q = generate_question(
            kpi_code=body.kpi_code, question_type=body.question_type,
            difficulty=body.difficulty, force_correct_answer=force, save_to_db=True
        )
        if q:
            results.append(q)
    return {"generated": len(results), "questions": results}


@app.post("/generate/all")
async def generate_all_route(
    body: GenerateAllRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """
    Kick off bulk generation for every KPI (or a subset).
    Runs in the background so the request returns immediately.
    Poll GET /generate/all/status to track progress.
    """
    global _bulk_job

    if _bulk_job["running"]:
        raise HTTPException(status_code=409, detail="A bulk generation job is already running.")

    from generator import load_kpi_knowledge_base, run_generation_batch

    kb      = load_kpi_knowledge_base()
    targets = body.kpi_codes if body.kpi_codes else list(kb.keys())
    total   = len(targets) * body.questions_per_kpi

    _bulk_job = {
        "running": True,
        "progress": 0,
        "total": total,
        "failed": 0,
        "done": False,
        "error": None,
    }

    def _run():
        global _bulk_job
        try:
            from generator import (
                load_kpi_knowledge_base, get_kpi_context,
                generate_question, check_answer_balance, _PLAN_20
            )

            kb2 = load_kpi_knowledge_base()

            def plan_for(n):
                base = _PLAN_20 * (n // len(_PLAN_20) + 1)
                return base[:n]

            for kpi_code in targets:
                kpi = kb2.get(kpi_code)
                if not kpi:
                    continue
                for q_type, diff in plan_for(body.questions_per_kpi):
                    balance = check_answer_balance(kpi["cluster"])
                    force   = balance["suggest"] if not balance["balanced"] else None
                    result  = generate_question(
                        kpi_code=kpi_code, question_type=q_type,
                        difficulty=diff, force_correct_answer=force, save_to_db=True
                    )
                    if result:
                        _bulk_job["progress"] += 1
                    else:
                        _bulk_job["failed"] += 1

            _bulk_job["running"] = False
            _bulk_job["done"]    = True

        except Exception as e:
            _bulk_job["running"] = False
            _bulk_job["error"]   = str(e)

    background_tasks.add_task(_run)

    return {
        "message": f"Bulk generation started for {len(targets)} KPIs × {body.questions_per_kpi} questions = {total} total.",
        "kpis": len(targets),
        "questions_per_kpi": body.questions_per_kpi,
        "total_target": total,
    }


@app.get("/generate/all/status")
async def generate_all_status(user=Depends(get_current_user)):
    """Poll this endpoint to check bulk generation progress."""
    return {
        "running":  _bulk_job["running"],
        "progress": _bulk_job["progress"],
        "total":    _bulk_job["total"],
        "failed":   _bulk_job["failed"],
        "done":     _bulk_job["done"],
        "error":    _bulk_job["error"],
        "percent":  round(_bulk_job["progress"] / _bulk_job["total"] * 100, 1)
                    if _bulk_job["total"] else 0,
    }


@app.get("/balance/{cluster}")
async def get_balance(cluster: str):
    from generator import check_answer_balance
    return check_answer_balance(cluster)



@app.get("/")
async def root():
    return {"status": "ok", "app": "Cluster Trainer API", "version": "1.0.0"}

@app.get("/health")
async def health():
    try:
        supabase.table("clusters").select("id").limit(1).execute()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}