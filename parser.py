"""
parser.py — Cluster Trainer PDF Parser
Extracts DECA questions from PDFs in /unstructured and saves to /structured + Supabase
"""

import os
import json
import re
import shutil
from pathlib import Path
from dotenv import load_dotenv
import pdfplumber
from supabase import create_client, Client

load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

BASE_DIR      = Path(__file__).parent
UNSTRUCTURED  = BASE_DIR / "unstructured"
STRUCTURED    = BASE_DIR / "structured"

UNSTRUCTURED.mkdir(exist_ok=True)
STRUCTURED.mkdir(exist_ok=True)


# ── REGEX PATTERNS ───────────────────────────────────────────────
RE_QUESTION  = re.compile(r'.+\?$', re.MULTILINE)
RE_ANSWER    = re.compile(r'^([A-D])[).]\s+(.+)', re.MULTILINE)
RE_CORRECT   = re.compile(r'(?:answer|key|correct)[:\s]+([A-D])', re.IGNORECASE)


def clean(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def parse_page(text: str) -> dict | None:
    """
    Try to extract one question object from a page of text.
    Returns None if the page doesn't contain a parseable question.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Find answer lines
    answers = {}
    answer_indices = []
    for i, line in enumerate(lines):
        m = RE_ANSWER.match(line)
        if m:
            answers[m.group(1).upper()] = clean(m.group(2))
            answer_indices.append(i)

    # Need all 4 answers
    if len(answers) < 4:
        return None

    # Find the question line (ends with ?)
    question_text = None
    question_idx  = None
    first_answer  = min(answer_indices)

    for i in range(first_answer - 1, -1, -1):
        if lines[i].endswith("?"):
            question_text = clean(lines[i])
            question_idx  = i
            break

    if not question_text:
        return None

    # Everything before the question is the scenario
    scenario_lines = lines[:question_idx] if question_idx else []
    # Filter out page numbers and short noise lines
    scenario_lines = [l for l in scenario_lines if len(l) > 20]
    scenario = clean(" ".join(scenario_lines)) if scenario_lines else None

    # Find correct answer (usually after the D) answer)
    last_answer_idx = max(answer_indices)
    remaining = " ".join(lines[last_answer_idx + 1:])
    correct_match = RE_CORRECT.search(remaining)
    correct = correct_match.group(1).upper() if correct_match else None

    # Explanation: text after correct answer marker
    explanation = None
    if correct_match:
        after = remaining[correct_match.end():].strip()
        if len(after) > 20:
            explanation = clean(after[:300])

    if not correct:
        return None

    return {
        "scenario":      scenario,
        "question":      question_text,
        "answer_a":      answers.get("A", ""),
        "answer_b":      answers.get("B", ""),
        "answer_c":      answers.get("C", ""),
        "answer_d":      answers.get("D", ""),
        "correct":       correct,
        "explanation":   explanation,
        "kpi_code":      None,   # to be tagged manually or via filename convention
        "cluster":       None,
        "question_type": "scenario" if scenario else "definition",
        "difficulty":    "medium",
        "source":        "parsed"
    }


def infer_cluster_from_filename(filename: str) -> tuple[str | None, str | None]:
    """
    Try to infer cluster and KPI code from filename.
    Naming convention: FI_062_questions.pdf → cluster=Finance, kpi_code=FI:062
    """
    name = Path(filename).stem.upper()

    cluster_map = {
        "FI": "Finance",
        "MK": "Marketing",
        "BL": "Business Law",
        "EC": "Economics",
        "EN": "Entrepreneurship",
        "MN": "Management",
        "HR": "Human Resources",
        "OP": "Operations",
        "IT": "Information Technology",
        "HO": "Hospitality",
        "SP": "Sports & Entertainment"
    }

    for prefix, cluster_name in cluster_map.items():
        if name.startswith(prefix):
            # try to extract number: FI_062 or FI062
            num_match = re.search(r'(\d{3})', name)
            if num_match:
                kpi_code = f"{prefix}:{num_match.group(1)}"
                return cluster_name, kpi_code
            return cluster_name, None

    return None, None


def parse_pdf(pdf_path: Path, cluster: str | None = None, kpi_code: str | None = None) -> list[dict]:
    """Parse all questions from a single PDF file."""
    questions = []

    # Try to infer from filename if not provided
    if not cluster or not kpi_code:
        inferred_cluster, inferred_kpi = infer_cluster_from_filename(pdf_path.name)
        cluster  = cluster  or inferred_cluster
        kpi_code = kpi_code or inferred_kpi

    print(f"  Parsing: {pdf_path.name} → cluster={cluster}, kpi={kpi_code}")

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text()
                if not text:
                    continue

                q = parse_page(text)
                if q:
                    q["cluster"]  = cluster
                    q["kpi_code"] = kpi_code
                    questions.append(q)
                    print(f"    Page {page_num}: ✓ found question")
                else:
                    print(f"    Page {page_num}: — no question")

    except Exception as e:
        print(f"  ✗ Error reading {pdf_path.name}: {e}")

    return questions


def save_questions(questions: list[dict], pdf_path: Path):
    """Save parsed questions to JSON and Supabase, then move PDF to /structured."""
    if not questions:
        print(f"  No questions to save for {pdf_path.name}")
        return

    # Save as JSON
    out_name = pdf_path.stem + "_parsed.json"
    out_path = STRUCTURED / out_name
    with open(out_path, "w") as f:
        json.dump(questions, f, indent=2)
    print(f"  ✓ Saved JSON: {out_path}")

    # Insert into Supabase
    valid = [q for q in questions if q.get("kpi_code") and q.get("cluster")]
    if valid:
        try:
            supabase.table("questions").insert(valid).execute()
            print(f"  ✓ Inserted {len(valid)} questions into Supabase")
        except Exception as e:
            print(f"  ✗ DB insert error: {e}")
    else:
        print(f"  ⚠ No questions had kpi_code + cluster — not inserted (tag manually)")

    # Move PDF to /structured
    dest = STRUCTURED / pdf_path.name
    shutil.move(str(pdf_path), str(dest))
    print(f"  ✓ Moved PDF to /structured/")


def run_parser():
    """Parse all PDFs in /unstructured."""
    pdfs = list(UNSTRUCTURED.glob("*.pdf"))

    if not pdfs:
        print(f"No PDFs found in {UNSTRUCTURED}")
        print("Drop your DECA exam PDFs into the /unstructured folder and run again.")
        return

    print(f"\n{'='*50}")
    print(f"  Cluster Trainer — PDF Parser")
    print(f"  Found {len(pdfs)} PDF(s) to parse")
    print(f"{'='*50}\n")

    all_questions = []
    for pdf_path in pdfs:
        print(f"\n► {pdf_path.name}")
        questions = parse_pdf(pdf_path)
        save_questions(questions, pdf_path)
        all_questions.extend(questions)
        print(f"  Extracted {len(questions)} questions")

    print(f"\n{'='*50}")
    print(f"  ✓ Done. Total questions extracted: {len(all_questions)}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run_parser()