import os
from flask import Flask, request, jsonify, abort, g
from flask_cors import CORS
from supabase import create_client, Client
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_ANON_KEY")
service_key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", key)

supabase: Client = create_client(url, key)
supabase_admin: Client = create_client(url, service_key)

app = Flask(__name__)
CORS(app)  # tighten origins in production


# ─────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────

def require_auth(f):
    """Decorator that validates the Bearer token and sets g.user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            abort(401, description="Missing or malformed Authorization header")
        token = auth_header.split(" ", 1)[1]
        try:
            user = supabase.auth.get_user(token)
            g.user = user.user
        except Exception:
            abort(401, description="Invalid or expired token")
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
# ERROR HANDLERS
# ─────────────────────────────────────────────

@app.errorhandler(400)
@app.errorhandler(401)
@app.errorhandler(404)
@app.errorhandler(422)
def handle_error(e):
    return jsonify({"detail": e.description}), e.code


# ─────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────

@app.post("/auth/signup")
def sign_up():
    body = request.get_json(force=True)
    email = body.get("email")
    password = body.get("password")
    if not email or not password:
        abort(400, description="email and password are required")
    try:
        response = supabase.auth.sign_up({"email": email, "password": password})
        user_id = response.user.id if response.user else None
        return jsonify({"message": "Check your email to confirm your account.", "user_id": user_id}), 201
    except Exception as e:
        abort(400, description=str(e))


@app.post("/auth/signin")
def sign_in():
    body = request.get_json(force=True)
    email = body.get("email")
    password = body.get("password")
    if not email or not password:
        abort(400, description="email and password are required")
    try:
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        return jsonify({
            "access_token": response.session.access_token,
            "token_type": "bearer",
            "user": {"id": response.user.id, "email": response.user.email}
        })
    except Exception:
        abort(401, description="Invalid email or password")


@app.post("/auth/signout")
@require_auth
def sign_out():
    supabase.auth.sign_out()
    return jsonify({"message": "Signed out successfully"})


# ─────────────────────────────────────────────
# CLUSTER / KPI ROUTES
# ─────────────────────────────────────────────

@app.get("/clusters")
def get_clusters():
    """Return all DECA clusters."""
    response = supabase.table("clusters").select("*").execute()
    return jsonify(response.data)


@app.get("/clusters/<cluster_id>/kpis")
def get_kpis_by_cluster(cluster_id):
    """Return all KPIs in a specific cluster."""
    response = (
        supabase.table("kpis")
        .select("*")
        .eq("cluster_id", cluster_id)
        .execute()
    )
    return jsonify(response.data)


@app.get("/kpis")
def get_all_kpis():
    """Return all KPIs across all clusters."""
    response = supabase.table("kpis").select("*, clusters(name)").execute()
    return jsonify(response.data)


@app.get("/kpis/<kpi_code>")
def get_kpi(kpi_code):
    """Return a single KPI by its code (e.g. FI:062)."""
    response = (
        supabase.table("kpis")
        .select("*")
        .eq("kpi_code", kpi_code)
        .single()
        .execute()
    )
    if not response.data:
        abort(404, description="KPI not found")
    return jsonify(response.data)


# ─────────────────────────────────────────────
# QUESTION ROUTES
# ─────────────────────────────────────────────

@app.get("/questions")
def get_questions():
    """Return questions with optional query-string filters."""
    cluster       = request.args.get("cluster")
    kpi_code      = request.args.get("kpi_code")
    difficulty    = request.args.get("difficulty")
    question_type = request.args.get("question_type")
    limit         = int(request.args.get("limit", 20))

    query = supabase.table("questions").select("*")
    if cluster:
        query = query.eq("cluster", cluster)
    if kpi_code:
        query = query.eq("kpi_code", kpi_code)
    if difficulty:
        query = query.eq("difficulty", difficulty)
    if question_type:
        query = query.eq("question_type", question_type)

    response = query.limit(limit).execute()
    return jsonify(response.data)


@app.get("/questions/<question_id>")
def get_question(question_id):
    """Return a single question by ID (answer hidden)."""
    response = (
        supabase.table("questions")
        .select("id, scenario, question, answer_a, answer_b, answer_c, answer_d, kpi_code, cluster, question_type, difficulty")
        .eq("id", question_id)
        .single()
        .execute()
    )
    if not response.data:
        abort(404, description="Question not found")
    return jsonify(response.data)


# ─────────────────────────────────────────────
# QUIZ / ATTEMPT ROUTES
# ─────────────────────────────────────────────

@app.post("/quiz/attempt")
@require_auth
def submit_attempt():
    """Submit a quiz answer and return whether it was correct."""
    body = request.get_json(force=True)
    question_id     = body.get("question_id")
    selected_answer = body.get("selected_answer")
    time_taken      = body.get("time_taken")

    if not question_id or not selected_answer:
        abort(400, description="question_id and selected_answer are required")

    q = (
        supabase.table("questions")
        .select("correct, explanation, kpi_code")
        .eq("id", question_id)
        .single()
        .execute()
    )
    if not q.data:
        abort(404, description="Question not found")

    is_correct = selected_answer.upper() == q.data["correct"].upper()

    supabase.table("quiz_history").insert({
        "user_id":         g.user.id,
        "question_id":     question_id,
        "kpi_code":        q.data["kpi_code"],
        "selected_answer": selected_answer.upper(),
        "is_correct":      is_correct,
        "time_taken":      time_taken,
    }).execute()

    return jsonify({
        "is_correct":      is_correct,
        "correct_answer":  q.data["correct"],
        "explanation":     q.data["explanation"]
    })


@app.get("/quiz/history")
@require_auth
def get_quiz_history():
    """Return the authenticated user's quiz history."""
    limit = int(request.args.get("limit", 50))
    response = (
        supabase.table("quiz_history")
        .select("*, questions(question, kpi_code, difficulty)")
        .eq("user_id", g.user.id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return jsonify(response.data)


@app.get("/quiz/stats")
@require_auth
def get_user_stats():
    """Return accuracy stats grouped by KPI for the heatmap."""
    response = (
        supabase.table("quiz_history")
        .select("kpi_code, is_correct")
        .eq("user_id", g.user.id)
        .execute()
    )

    stats: dict = {}
    for row in response.data:
        kpi = row["kpi_code"]
        if kpi not in stats:
            stats[kpi] = {"total": 0, "correct": 0}
        stats[kpi]["total"] += 1
        if row["is_correct"]:
            stats[kpi]["correct"] += 1

    result = [
        {
            "kpi_code": kpi,
            "total":    v["total"],
            "correct":  v["correct"],
            "accuracy": round(v["correct"] / v["total"] * 100, 1) if v["total"] else 0
        }
        for kpi, v in stats.items()
    ]
    return jsonify(result)


# ─────────────────────────────────────────────
# GENERATION ROUTES
# ─────────────────────────────────────────────

@app.post("/generate")
@require_auth
def generate_questions_route():
    """Generate new questions using Gemini + RAG. Protected route."""
    from generator import generate_question, check_answer_balance

    body          = request.get_json(force=True)
    kpi_code      = body.get("kpi_code")
    question_type = body.get("question_type")
    difficulty    = body.get("difficulty")
    count         = int(body.get("count", 1))

    if not kpi_code or not question_type or not difficulty:
        abort(400, description="kpi_code, question_type, and difficulty are required")

    results = []
    for _ in range(count):
        balance = check_answer_balance(kpi_code.split(":")[0])
        force   = balance["suggest"] if not balance["balanced"] else None
        q = generate_question(
            kpi_code=kpi_code,
            question_type=question_type,
            difficulty=difficulty,
            force_correct_answer=force,
            save_to_db=True
        )
        if q:
            results.append(q)

    return jsonify({"generated": len(results), "questions": results})


@app.get("/balance/<cluster>")
def get_balance(cluster):
    """Check answer distribution balance for a cluster."""
    from generator import check_answer_balance
    return jsonify(check_answer_balance(cluster))


# ─────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────

@app.get("/")
def root():
    return jsonify({"status": "ok", "app": "Cluster Trainer API", "version": "1.0.0"})


@app.get("/health")
def health():
    try:
        supabase.table("clusters").select("id").limit(1).execute()
        return jsonify({"status": "healthy", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.get("/app")
@app.get("/app/")
def serve_frontend_root():
    from flask import redirect
    return redirect("/app/index.html")


@app.get("/app/<path:filename>")
def serve_frontend_file(filename):
    """
    Serve any frontend file from the folder containing this script,
    or a subfolder if FRONTEND_DIR is set in .env.

    File layout options:
      A) Flat (default): app_flask.py, index.html, dashboard.html,
                         selector.html, quiz.html, results.html,
                         settings.html, styles.css, common.js all together.
      B) Subfolder: put HTML/CSS/JS in a frontend/ folder and add
                    FRONTEND_DIR=frontend to your .env
    """
    import pathlib
    from flask import send_from_directory
    base = pathlib.Path(__file__).parent
    frontend_dir = os.environ.get("FRONTEND_DIR", "")
    folder = str(base / frontend_dir) if frontend_dir else str(base)
    safe_ext = {".html", ".css", ".js", ".ico", ".png", ".svg", ".jpg", ".webp"}
    if pathlib.Path(filename).suffix.lower() not in safe_ext:
        abort(404, description="File not found")
    return send_from_directory(folder, filename)


if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 8000))
    debug = os.environ.get("FLASK_ENV") == "development"
    print(f"\n  Local app -> http://localhost:{port}/app\n")
    app.run(host="0.0.0.0", port=port, debug=debug)