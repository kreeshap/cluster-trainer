"""
Entrypoint — set FRAMEWORK env var to switch:

  FRAMEWORK=flask    → Flask  (gunicorn server:app)
  FRAMEWORK=fastapi  → FastAPI (uvicorn server:app)

Render start commands:
  Flask:   gunicorn server:app
  FastAPI: uvicorn server:app --host 0.0.0.0 --port $PORT
"""

import os

FRAMEWORK = os.environ.get("FRAMEWORK", "fastapi").lower()

if FRAMEWORK == "flask":
    from app_flask import app          # Flask app object
else:
    from main import app               # FastAPI app object