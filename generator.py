"""
generator.py — Cluster Trainer Question Generator
Uses Google Gemini (free) + RAG from kpi_knowledge_base.json + parsed question examples
"""

import os
import json
import random
import re
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
    """Load the full KPI knowledge base from JSON."""
    with open(KB_PATH, "r") as f:
        data = json.load(f)
    # Index by kpi_code for fast lookup
    return {kpi["kpi_code"]: kpi for kpi in data["kpis"]}


def get_kpi_context(kpi_code: str) -> dict | None:
    """Return the knowledge base entry for one KPI."""
    kb = load_kpi_knowledge_base()
    return kb.get(kpi_code)


# ════════════════════════════════════════════════════════════════
#  STEP 2 — STYLE EXAMPLES (RAG layer)
# ════════════════════════════════════════════════════════════════

def get_style_examples(cluster: str, question_type: str, n: int = 5) -> list[dict]:
    """
    Fetch real parsed questions from the database filtered by cluster + type.
    These are injected as style examples ONLY — not for content.
    Falls back to any cluster if not enough examples found.
    """
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

    # fallback: any parsed question from any cluster
    if len(examples) < 3:
        fallback = (
            supabase.table("questions")
            .select("scenario, question, answer_a, answer_b, answer_c, answer_d, correct, explanation")
            .eq("source", "parsed")
            .limit(20)
            .execute()
        )
        examples = (fallback.data or []) + examples

    # pick n random examples
    return random.sample(examples, min(n, len(examples)))


# ════════════════════════════════════════════════════════════════
#  STEP 3 — ANSWER BALANCE CHECKER
# ════════════════════════════════════════════════════════════════

def check_answer_balance(cluster: str) -> dict:
    """
    Check if correct answers are evenly distributed across A/B/C/D.
    Returns the underrepresented letter(s) if any letter exceeds 30%.
    """
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


# ════════════════════════════════════════════════════════════════
#  STEP 4 — PROMPT BUILDER
# ════════════════════════════════════════════════════════════════

def build_prompt(
    kpi: dict,
    question_type: str,
    difficulty: str,
    style_examples: list[dict],
    force_correct_answer: str | None = None
) -> str:

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

    angle_key = f"{difficulty}_angle"
    angle = kpi.get(angle_key, f"A {difficulty} level question about {kpi['kpi_name']}")

    prompt = f"""You are an expert DECA exam question writer. Your job is to write high-quality, realistic DECA-style multiple choice questions.

══ KPI KNOWLEDGE (use this as your content source) ══
KPI Code: {kpi['kpi_code']}
KPI Name: {kpi['kpi_name']}
Cluster: {kpi['cluster']}
Definition: {kpi['definition']}
Formula: {kpi.get('formula', 'N/A')}
Real World Context: {kpi['real_world_context']}
Common Misconceptions: {kpi['common_misconceptions']}
{difficulty.capitalize()} Angle to Use: {angle}

══ STYLE EXAMPLES (use these for format and tone ONLY — not content) ══
{examples_text if examples_text else "No examples available — write in standard DECA exam style."}

══ YOUR TASK ══
Write ONE new DECA-style question with these specifications:
- Question Type: {question_type}
- Difficulty: {difficulty}
- Topic: {kpi['kpi_name']} ({kpi['kpi_code']})
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
  "kpi_code": "{kpi['kpi_code']}",
  "cluster": "{kpi['cluster']}",
  "question_type": "{question_type}",
  "difficulty": "{difficulty}",
  "source": "generated"
}}"""

    return prompt


# ════════════════════════════════════════════════════════════════
#  STEP 5 — GENERATE ONE QUESTION
# ════════════════════════════════════════════════════════════════

def generate_question(
    kpi_code: str,
    question_type: str,
    difficulty: str,
    force_correct_answer: str | None = None,
    save_to_db: bool = True
) -> dict | None:
    """
    Generate one DECA question using Gemini + RAG.
    
    Args:
        kpi_code:            e.g. "FI:062"
        question_type:       "calculation" | "scenario" | "definition" | "application"
        difficulty:          "easy" | "medium" | "hard"
        force_correct_answer: "A" | "B" | "C" | "D" — force a specific correct answer
        save_to_db:          whether to insert the result into Supabase
    
    Returns:
        The generated question dict, or None on failure
    """
    # 1. Load KPI context
    kpi = get_kpi_context(kpi_code)
    if not kpi:
        print(f"  ✗ KPI not found: {kpi_code}")
        return None

    # 2. Get style examples
    examples = get_style_examples(kpi["cluster"], question_type)

    # 3. Build prompt
    prompt = build_prompt(kpi, question_type, difficulty, examples, force_correct_answer)

    # 4. Call Groq
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  ✗ Groq API error: {e}")
        return None

    # 5. Parse JSON response
    try:
        # Strip markdown fences if present
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        question = json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"  ✗ JSON parse error: {e}")
        print(f"  Raw response: {raw[:200]}")
        return None

    # 6. Validate required fields
    required = ["question", "answer_a", "answer_b", "answer_c", "answer_d", "correct"]
    for field in required:
        if field not in question:
            print(f"  ✗ Missing field: {field}")
            return None

    # 7. Save to database
    if save_to_db:
        try:
            result = supabase.table("questions").insert(question).execute()
            question["id"] = result.data[0]["id"] if result.data else None
            print(f"  ✓ Saved to DB: {kpi_code} [{question_type}/{difficulty}] correct={question['correct']}")
        except Exception as e:
            print(f"  ✗ DB insert error: {e}")

    return question


# ════════════════════════════════════════════════════════════════
#  STEP 6 — BATCH GENERATION (run_generation.py logic)
# ════════════════════════════════════════════════════════════════

def run_generation_batch(
    kpi_codes: list[str] | None = None,
    questions_per_kpi: int = 5,
    check_balance_every: int = 50
):
    """
    Generate questions for all KPIs in the knowledge base.
    Runs answer balance check every N questions.
    
    Args:
        kpi_codes:            Specific KPI codes to generate for. None = all KPIs.
        questions_per_kpi:    How many questions to generate per KPI.
        check_balance_every:  Run balance check after every N questions generated.
    """
    kb = load_kpi_knowledge_base()
    targets = kpi_codes if kpi_codes else list(kb.keys())

    question_types = ["definition", "scenario", "application", "calculation", "scenario"]
    difficulties   = ["easy", "medium", "hard", "medium", "hard"]

    total_generated = 0
    total_target    = len(targets) * questions_per_kpi
    clusters_seen   = set()

    print(f"\n{'='*55}")
    print(f"  Cluster Trainer — Question Generation Batch")
    print(f"  KPIs: {len(targets)}  |  Per KPI: {questions_per_kpi}  |  Total: {total_target}")
    print(f"{'='*55}\n")

    for kpi_code in targets:
        kpi = kb.get(kpi_code)
        if not kpi:
            print(f"Skipping unknown KPI: {kpi_code}")
            continue

        print(f"► {kpi_code} — {kpi['kpi_name']}")
        clusters_seen.add(kpi["cluster"])

        for i in range(questions_per_kpi):
            q_type  = question_types[i % len(question_types)]
            diff    = difficulties[i % len(difficulties)]

            # Check balance to decide if we should force a letter
            balance = check_answer_balance(kpi["cluster"])
            force   = balance["suggest"] if not balance["balanced"] else None

            print(f"  [{i+1}/{questions_per_kpi}] {q_type}/{diff}", end="")
            if force:
                print(f" (forcing correct={force})", end="")
            print()

            result = generate_question(kpi_code, q_type, diff, force_correct_answer=force)

            if result:
                total_generated += 1

            # Balance check every N questions
            if total_generated > 0 and total_generated % check_balance_every == 0:
                print(f"\n  ── Balance Check at {total_generated} questions ──")
                for cluster in clusters_seen:
                    b = check_answer_balance(cluster)
                    print(f"  {cluster}: {b['percentages']}")
                    if not b["balanced"]:
                        print(f"    ⚠ Over-represented: {b['overrepresented']}")
                print()

        print(f"  Generated {total_generated}/{total_target} total\n")

    print(f"\n{'='*55}")
    print(f"  ✓ Complete: {total_generated}/{total_target} questions generated")
    print(f"{'='*55}")

    # Final balance report
    print("\n── Final Answer Balance Report ──")
    for cluster in clusters_seen:
        b = check_answer_balance(cluster)
        status = "✓ Balanced" if b["balanced"] else "⚠ Unbalanced"
        print(f"  {cluster}: {b['percentages']} {status}")


# ════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # e.g. python generator.py FI:062 FI:064
        kpi_list = sys.argv[1:]
        run_generation_batch(kpi_codes=kpi_list, questions_per_kpi=5)
    else:
        # Generate for all KPIs in the knowledge base
        run_generation_batch(questions_per_kpi=5)