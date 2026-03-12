import os
import pathlib
from typing import Optional, List
from functools import wraps

from flask import Flask, request, jsonify, redirect, send_from_directory
from flask_cors import CORS
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

app = Flask(__name__, static_folder=str(_base / "static"), template_folder=str(_base / "templates"))
CORS(app)

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
def get_current_user():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None, (jsonify({"detail": "Missing or invalid authorization header"}), 401)
    token = auth_header.split(" ", 1)[1]
    try:
        user = supabase.auth.get_user(token)
        return user.user, None
    except Exception:
        return None, (jsonify({"detail": "Invalid or expired token"}), 401)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user, err = get_current_user()
        if err:
            return err
        return f(*args, user=user, **kwargs)
    return decorated

# ── API ROUTES ────────────────────────────────────────────

# AUTH
@app.post("/auth/signup")
def sign_up():
    body = SignUpRequest(**request.get_json())
    try:
        res = supabase.auth.sign_up({"email": body.email, "password": body.password})
        return jsonify({"message": "Check email to confirm.", "user_id": res.user.id if res.user else None})
    except Exception as e:
        return jsonify({"detail": str(e)}), 400

@app.post("/auth/signin")
def sign_in():
    body = SignInRequest(**request.get_json())
    try:
        res = supabase.auth.sign_in_with_password({"email": body.email, "password": body.password})
        return jsonify({"access_token": res.session.access_token, "user": res.user})
    except Exception:
        return jsonify({"detail": "Invalid credentials"}), 401

# CLUSTERS & KPIs
@app.get("/clusters")
def get_clusters():
    return jsonify(supabase.table("clusters").select("*").execute().data)

@app.get("/kpis")
def get_all_kpis():
    return jsonify(supabase.table("kpis").select("*, clusters(name)").execute().data)

@app.get("/kpis/<kpi_code>")
def get_kpi(kpi_code: str):
    res = supabase.table("kpis").select("*").eq("kpi_code", kpi_code).single().execute()
    if not res.data:
        return jsonify({"detail": "KPI not found"}), 404
    return jsonify(res.data)

# QUESTIONS
@app.get("/questions")
def get_questions():
    cluster = request.args.get("cluster")
    kpi_code = request.args.get("kpi_code")
    limit = int(request.args.get("limit", 20))
    query = supabase.table("questions").select("*")
    if cluster:
        query = query.eq("cluster", cluster)
    if kpi_code:
        query = query.eq("kpi_code", kpi_code)
    return jsonify(query.limit(limit).execute().data)

# QUIZ
@app.post("/quiz/attempt")
@require_auth
def submit_attempt(user):
    body = QuizAttemptRequest(**request.get_json())
    q = supabase.table("questions").select("correct, explanation, kpi_code").eq("id", body.question_id).single().execute()
    if not q.data:
        return jsonify({"detail": "Question not found"}), 404

    is_correct = body.selected_answer.upper() == q.data["correct"].upper()
    supabase.table("quiz_history").insert({
        "user_id": user.id, "question_id": body.question_id, "kpi_code": q.data["kpi_code"],
        "selected_answer": body.selected_answer.upper(), "is_correct": is_correct, "time_taken": body.time_taken
    }).execute()
    return jsonify({"is_correct": is_correct, "correct_answer": q.data["correct"], "explanation": q.data["explanation"]})

@app.get("/quiz/history")
@require_auth
def get_history(user):
    return jsonify(
        supabase.table("quiz_history")
        .select("*, questions(*)")
        .eq("user_id", user.id)
        .order("created_at", desc=True)
        .execute().data
    )

# GENERATION
@app.post("/generate")
@require_auth
def generate(user):
    body = GenerateRequest(**request.get_json())
    from generator import generate_question
    q = generate_question(kpi_code=body.kpi_code, question_type=body.question_type, difficulty=body.difficulty, save_to_db=True)
    return jsonify({"generated": 1, "question": q})

@app.get("/health")
def health():
    return jsonify({"status": "healthy"})

# ── FRONTEND (MUST BE LAST) ───────────────────────────────
@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(str(_base / "static"), filename)

@app.route("/")
def redirect_to_index():
    return redirect("/app/")

@app.route("/app/")
@app.route("/app/<path:filename>")
def serve_frontend(filename="index.html"):
    templates_dir = str(_base / "templates")
    return send_from_directory(templates_dir, filename)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)