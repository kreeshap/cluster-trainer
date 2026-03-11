"""
generator.py — Cluster Trainer Question Generator
Uses Groq + RAG from kpi_knowledge_base.json + parsed question examples
"""

import os
import json
import random
import re
import time
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from supabase import create_client, Client

load_dotenv()

# ── CLIENTS ──────────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

# ── PATHS ────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
KB_PATH   = BASE_DIR / "kpi_knowledge_base.json"


# ════════════════════════════════════════════════════════════════
#  STEP 1 — LOAD KPI KNOWLEDGE BASE
# ════════════════════════════════════════════════════════════════

def load_kpi_knowledge_base() -> dict:
    """Supports both flat {"kpis": [...]} and clustered {"clusters": {...}} formats."""
    with open(KB_PATH, "r") as f:
        data = json.load(f)

    kpis = []
    if "kpis" in data:
        kpis = data["kpis"]
    elif "clusters" in data:
        for cluster_name, cluster_kpis in data["clusters"].items():
            for kpi in cluster_kpis:
                kpi_copy = dict(kpi)
                kpi_copy["cluster"] = cluster_name
                kpis.append(kpi_copy)
    else:
        raise KeyError("kpi_knowledge_base.json must have either a 'kpis' or 'clusters' top-level key")

    return {kpi["kpi_code"]: kpi for kpi in kpis}


def get_kpi_context(kpi_code: str) -> dict | None:
    kb = load_kpi_knowledge_base()
    return kb.get(kpi_code)


# ════════════════════════════════════════════════════════════════
#  STEP 2 — CHECK EXISTING QUESTIONS (skip completed KPIs)
# ════════════════════════════════════════════════════════════════

def get_existing_counts(kpi_codes: list[str]) -> dict[str, int]:
    """Returns {kpi_code: count} of already-generated questions in the DB."""
    if not kpi_codes:
        return {}
    try:
        response = (
            supabase.table("questions")
            .select("kpi_code")
            .in_("kpi_code", kpi_codes)
            .eq("source", "generated")
            .execute()
        )
        counts: dict[str, int] = {}
        for row in (response.data or []):
            code = row["kpi_code"]
            counts[code] = counts.get(code, 0) + 1
        return counts
    except Exception as e:
        print(f"⚠ Could not fetch existing counts: {e}")
        return {}


def get_style_examples(cluster: str, question_type: str, n: int = 5) -> list[dict]:
    try:
        response = (
            supabase.table("questions")
            .select("scenario, question, answer_a, answer_b, answer_c, answer_d, correct, explanation")
            .eq("cluster", cluster)
            .eq("question_type", question_type)
            .eq("source", "parsed")
            .limit(20)
            .execute()
        )
        examples = response.data or []
        if len(examples) < 3:
            fallback = (
                supabase.table("questions")
                .select("scenario, question, answer_a, answer_b, answer_c, answer_d, correct, explanation")
                .eq("source", "parsed")
                .limit(20)
                .execute()
            )
            examples = (fallback.data or []) + examples
        return random.sample(examples, min(n, len(examples)))
    except Exception:
        return []


def check_answer_balance(cluster: str) -> dict:
    try:
        response = (
            supabase.table("questions")
            .select("correct")
            .eq("cluster", cluster)
            .execute()
        )
        counts = {"A": 0, "B": 0, "C": 0, "D": 0}
        for row in (response.data or []):
            letter = row["correct"].upper()
            if letter in counts:
                counts[letter] += 1
        total = sum(counts.values())
        if total == 0:
            return {"balanced": True, "counts": counts, "suggest": None}
        overrepresented  = [k for k, v in counts.items() if v / total > 0.30]
        underrepresented = [k for k, v in counts.items() if v / total < 0.20]
        return {
            "balanced": len(overrepresented) == 0,
            "counts": counts,
            "percentages": {k: round(v / total * 100, 1) for k, v in counts.items()},
            "overrepresented": overrepresented,
            "suggest": underrepresented[0] if underrepresented else None
        }
    except Exception:
        return {"balanced": True, "counts": {}, "suggest": None}


def build_prompt(kpi, question_type, difficulty, style_examples, force_correct_answer=None):
    examples_text = ""
    for i, ex in enumerate(style_examples, 1):
        examples_text += f"""
Example {i}:
Scenario: {ex.get('scenario', 'N/A')}
Question: {ex['question']}
A) {ex['answer_a']}
B) {ex['answer_b']}
C) {ex['answer_c']}
D) {ex['answer_d']}
Correct: {ex['correct']}
Explanation: {ex.get('explanation', '')}
"""

    force_instruction = ""
    if force_correct_answer:
        force_instruction = f"\nIMPORTANT: The correct answer MUST be option {force_correct_answer}. Design the question and answers so that {force_correct_answer} is the best answer.\n"

    kpi_name       = kpi.get("kpi_name", "")
    kpi_code       = kpi.get("kpi_code", "")
    cluster        = kpi.get("cluster", kpi.get("instructional_area", ""))
    definition     = kpi.get("definition", f"Understand and apply: {kpi_name}")
    formula        = kpi.get("formula", "N/A")
    real_world     = kpi.get("real_world_context", f"This KPI applies to real-world business scenarios in {cluster}.")
    misconceptions = kpi.get("common_misconceptions", "Students often confuse related concepts or misapply terminology.")
    angle          = kpi.get(f"{difficulty}_angle", f"A {difficulty}-level question about {kpi_name}")

    return f"""You are an expert DECA exam question writer. Your job is to write high-quality, realistic DECA-style multiple choice questions.

══ KPI KNOWLEDGE (use this as your content source) ══
KPI Code: {kpi_code}
KPI Name: {kpi_name}
Cluster: {cluster}
Definition: {definition}
Formula: {formula}
Real World Context: {real_world}
Common Misconceptions: {misconceptions}
{difficulty.capitalize()} Angle to Use: {angle}

{examples_text if examples_text else "No examples available — write in standard DECA exam style."}

══ YOUR TASK ══
Write ONE new DECA-style question with these specifications:
- Question Type: {question_type}
- Difficulty: {difficulty}
- Topic: {kpi_name} ({kpi_code})
{force_instruction}

Rules:
1. Write a realistic business scenario (2-4 sentences) if question_type is "scenario" or "calculation"
2. The question should end with a question mark
3. All four answer choices must be plausible — wrong answers should be common misconceptions or close alternatives
4. Only ONE answer should be clearly correct
5. The explanation should be 1-2 sentences explaining WHY the correct answer is right
6. Match the length and tone of the style examples above
7. Do NOT copy content from the style examples

Return ONLY valid JSON in this exact format — no markdown, no extra text:
{{
  "scenario": "Business scenario text here or null if not applicable",
  "question": "The question text ending with a question mark?",
  "answer_a": "First answer choice",
  "answer_b": "Second answer choice",
  "answer_c": "Third answer choice",
  "answer_d": "Fourth answer choice",
  "correct": "A",
  "explanation": "Brief explanation of why the correct answer is right.",
  "kpi_code": "{kpi_code}",
  "cluster": "{cluster}",
  "question_type": "{question_type}",
  "difficulty": "{difficulty}",
  "source": "generated"
}}"""


def call_groq_with_retry(prompt: str, max_retries: int = 8) -> str | None:
    """Call Groq API with automatic retry on rate limit (429)."""
    for attempt in range(max_retries):
        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit_exceeded" in err:
                # Parse wait time from error message
                wait = 135  # safe default
                match = re.search(r'try again in (\d+)m([\d.]+)s', err)
                if match:
                    wait = int(match.group(1)) * 60 + float(match.group(2)) + 5
                print(f"\n  ⏳ Rate limit — waiting {int(wait)}s "
                      f"(attempt {attempt + 1}/{max_retries})...", flush=True)
                time.sleep(wait)
            else:
                print(f"  ✗ Groq API error: {e}")
                return None

    print(f"  ✗ Max retries exceeded")
    return None


def generate_question(
    kpi_code: str,
    question_type: str,
    difficulty: str,
    force_correct_answer: str | None = None,
    save_to_db: bool = True
) -> dict | None:

    kpi = get_kpi_context(kpi_code)
    if not kpi:
        print(f"  ✗ KPI not found: {kpi_code}")
        return None

    cluster  = kpi.get("cluster", kpi.get("instructional_area", ""))
    examples = get_style_examples(cluster, question_type)
    prompt   = build_prompt(kpi, question_type, difficulty, examples, force_correct_answer)

    raw = call_groq_with_retry(prompt)
    if raw is None:
        return None

    try:
        clean    = re.sub(r"```(?:json)?|```", "", raw).strip()
        question = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error: {e}")
        print(f"  Raw: {raw[:200]}")
        return None

    required = ["question", "answer_a", "answer_b", "answer_c", "answer_d", "correct"]
    for field in required:
        if field not in question:
            print(f"  ✗ Missing field: {field}")
            return None

    # Sanitize correct field — must be a single A/B/C/D
    correct = question["correct"].strip().upper()
    question["correct"] = correct[0] if correct else "A"
    if question["correct"] not in ("A", "B", "C", "D"):
        print(f"  ✗ Invalid correct answer: {question['correct']}")
        return None

    if save_to_db:
        try:
            result = supabase.table("questions").insert(question).execute()
            question["id"] = result.data[0]["id"] if result.data else None
            print(f"  ✓ Saved: {kpi_code} [{question_type}/{difficulty}] correct={question['correct']}")
        except Exception as e:
            print(f"  ✗ DB insert error: {e}")

    return question


_PLAN_20: list[tuple[str, str]] = [
    ("definition",   "easy"),
    ("definition",   "medium"),
    ("definition",   "hard"),
    ("definition",   "medium"),
    ("scenario",     "easy"),
    ("scenario",     "easy"),
    ("scenario",     "medium"),
    ("scenario",     "medium"),
    ("scenario",     "hard"),
    ("scenario",     "hard"),
    ("application",  "easy"),
    ("application",  "medium"),
    ("application",  "medium"),
    ("application",  "hard"),
    ("calculation",  "easy"),
    ("calculation",  "easy"),
    ("calculation",  "medium"),
    ("calculation",  "medium"),
    ("calculation",  "hard"),
    ("calculation",  "hard"),
]


def run_generation_batch(
    kpi_codes: list[str] | None = None,
    questions_per_kpi: int = 20,
    check_balance_every: int = 50,
):
    kb      = load_kpi_knowledge_base()
    targets = kpi_codes if kpi_codes else list(kb.keys())

    # ── CHECK WHICH KPIs ARE ALREADY DONE ──────────────────────
    print("\n  Checking existing question counts in DB...", flush=True)
    existing = get_existing_counts(targets)

    done_kpis    = [k for k in targets if existing.get(k, 0) >= questions_per_kpi]
    pending_kpis = [k for k in targets if existing.get(k, 0) < questions_per_kpi]

    print(f"  ✓ Already complete : {len(done_kpis)} KPIs  (skipping)")
    print(f"  → Needs generation : {len(pending_kpis)} KPIs")

    def plan_for(n: int) -> list[tuple[str, str]]:
        base = _PLAN_20 * (n // len(_PLAN_20) + 1)
        return base[:n]

    total_target    = sum(questions_per_kpi - existing.get(k, 0) for k in pending_kpis)
    total_generated = 0
    total_failed    = 0
    clusters_seen: set[str] = set()

    print(f"\n{'='*60}")
    print(f"  Cluster Trainer — Bulk Question Generation")
    print(f"  KPIs pending : {len(pending_kpis)}")
    print(f"  Questions    : {total_target} remaining to generate")
    print(f"{'='*60}\n")

    for idx, kpi_code in enumerate(pending_kpis):
        kpi = kb.get(kpi_code)
        if not kpi:
            print(f"⚠ Skipping unknown KPI: {kpi_code}")
            continue

        cluster  = kpi.get("cluster", kpi.get("instructional_area", "Unknown"))
        kpi_name = kpi.get("kpi_name", kpi_code)
        already  = existing.get(kpi_code, 0)
        need     = questions_per_kpi - already

        print(f"\n► [{idx + 1}/{len(pending_kpis)}] {kpi_code} — {kpi_name}")
        if already > 0:
            print(f"  (resuming: {already} already saved, generating {need} more)")

        clusters_seen.add(cluster)
        # Skip the slots already covered
        plan          = plan_for(questions_per_kpi)[already:]
        kpi_generated = 0

        for i, (q_type, diff) in enumerate(plan):
            slot_num = already + i + 1
            balance  = check_answer_balance(cluster)
            force    = balance["suggest"] if not balance["balanced"] else None

            label = f"[{slot_num:02d}/{questions_per_kpi}] {q_type:<12} {diff:<8}"
            if force:
                label += f" → force={force}"
            print(f"  {label}", end=" ", flush=True)

            result = generate_question(
                kpi_code=kpi_code,
                question_type=q_type,
                difficulty=diff,
                force_correct_answer=force,
                save_to_db=True,
            )

            if result:
                total_generated += 1
                kpi_generated   += 1
            else:
                total_failed += 1
                print("  ✗ FAILED")

            if total_generated > 0 and total_generated % check_balance_every == 0:
                print(f"\n  ── Balance Check @ {total_generated} questions ──")
                for c in clusters_seen:
                    b      = check_answer_balance(c)
                    status = "✓" if b["balanced"] else "⚠"
                    print(f"  {status} {c}: {b.get('percentages', {})}")
                print()

        print(f"  └─ KPI done: {kpi_generated}/{need} new saved  "
              f"(running total: {total_generated}/{total_target})")

    print(f"\n{'='*60}")
    print(f"  ✓ Generation complete")
    print(f"  Generated : {total_generated}")
    print(f"  Failed    : {total_failed}")
    print(f"  Skipped   : {len(done_kpis)} KPIs (already had {questions_per_kpi}+ questions)")
    print(f"{'='*60}")

    print("\n── Final Answer Balance Report ──")
    for cluster in sorted(clusters_seen):
        b      = check_answer_balance(cluster)
        status = "✓ Balanced" if b["balanced"] else "⚠ Unbalanced"
        print(f"  {cluster}: {b.get('percentages', {})}  {status}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        run_generation_batch(kpi_codes=sys.argv[1:], questions_per_kpi=20)
    else:
        run_generation_batch(questions_per_kpi=20)