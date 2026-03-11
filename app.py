import os

import pathlib

from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, status

from fastapi.middleware.cors import CORSMiddleware

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from fastapi.responses import RedirectResponse

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from supabase import create_client, Client

from dotenv import load_dotenv



load_dotenv()



# ── SETUP ──────────────────────────────────────────────────

_base = pathlib.Path(__file__).parent

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

_bulk_job = {"running": False, "progress": 0, "total": 0, "failed": 0, "done": False, "error": None}



# ── MODELS ────────────────────────────────────────────────

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

    kpi_codes: Optional[List[str]] = None

    questions_per_kpi: int = 20



# ── AUTH HELPERS ──────────────────────────────────────────

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):

    try:

        user = supabase.auth.get_user(credentials.credentials)

        return user.user

    except Exception:

        raise HTTPException(status_code=401, detail="Invalid or expired token")



# ── FRONTEND ROUTING ──────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(_base / "static")), name="static")



@app.get("/")

async def redirect_to_index():

    return RedirectResponse(url="/app/")



app.mount("/app", StaticFiles(directory=str(_base / "templates"), html=True), name="frontend")



# ── API ROUTES ────────────────────────────────────────────



# AUTH

@app.post("/auth/signup")

async def sign_up(body: SignUpRequest):

    try:

        res = supabase.auth.sign_up({"email": body.email, "password": body.password})

        return {"message": "Check email to confirm.", "user_id": res.user.id if res.user else None}

    except Exception as e: raise HTTPException(status_code=400, detail=str(e))



@app.post("/auth/signin")

async def sign_in(body: SignInRequest):

    try:

        res = supabase.auth.sign_in_with_password({"email": body.email, "password": body.password})

        return {"access_token": res.session.access_token, "user": res.user}

    except Exception: raise HTTPException(status_code=401, detail="Invalid credentials")



# CLUSTERS & KPIs

@app.get("/clusters")

async def get_clusters():

    return supabase.table("clusters").select("*").execute().data



@app.get("/kpis")

async def get_all_kpis():

    return supabase.table("kpis").select("*, clusters(name)").execute().data



@app.get("/kpis/{kpi_code}")

async def get_kpi(kpi_code: str):

    res = supabase.table("kpis").select("*").eq("kpi_code", kpi_code).single().execute()

    if not res.data: raise HTTPException(status_code=404, detail="KPI not found")

    return res.data



# QUESTIONS

@app.get("/questions")

async def get_questions(cluster: str = None, kpi_code: str = None, limit: int = 20):

    query = supabase.table("questions").select("*")

    if cluster: query = query.eq("cluster", cluster)

    if kpi_code: query = query.eq("kpi_code", kpi_code)

    return query.limit(limit).execute().data



# QUIZ

@app.post("/quiz/attempt")

async def submit_attempt(body: QuizAttemptRequest, user=Depends(get_current_user)):

    q = supabase.table("questions").select("correct, explanation, kpi_code").eq("id", body.question_id).single().execute()

    if not q.data: raise HTTPException(status_code=404, detail="Question not found")

    

    is_correct = body.selected_answer.upper() == q.data["correct"].upper()

    supabase.table("quiz_history").insert({

        "user_id": user.id, "question_id": body.question_id, "kpi_code": q.data["kpi_code"],

        "selected_answer": body.selected_answer.upper(), "is_correct": is_correct, "time_taken": body.time_taken

    }).execute()

    return {"is_correct": is_correct, "correct_answer": q.data["correct"], "explanation": q.data["explanation"]}



@app.get("/quiz/history")

async def get_history(user=Depends(get_current_user)):

    return supabase.table("quiz_history").select("*, questions(*)").eq("user_id", user.id).order("created_at", desc=True).execute().data



# GENERATION

@app.post("/generate")

async def generate(body: GenerateRequest, user=Depends(get_current_user)):

    from generator import generate_question

    q = generate_question(kpi_code=body.kpi_code, question_type=body.question_type, difficulty=body.difficulty, save_to_db=True)

    return {"generated": 1, "question": q}



@app.get("/health")

async def health():

    return {"status": "healthy"}



if __name__ == "__main__":

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)