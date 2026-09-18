"""
Stage 6 & Lead Generation Portal — Master Web Application

Serves:
  - Secure Session Authentication (/login, /logout)
  - Full Analytics Dashboard & Stages Explorer (/)
  - Touch-Optimized Mobile Action Queue (/queue)
  - UI Pipeline Controller (Run Stages 1–5 without CLI)
  - REST APIs for Telemetry, Stage Filtering, and 1-Tap Mobile Dispatches
"""

import hmac
import hashlib
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from config import (
    QUEUE_DAILY_LIMIT,
    QUEUE_HOST,
    QUEUE_PORT,
    PORTAL_AUTH_USERNAME,
    PORTAL_AUTH_PASSWORD,
    PORTAL_SECRET_KEY,
    DATABASE_URL,
    Status,
)
from db import (
    get_connection,
    init_db,
    get_pipeline_stats,
    get_leads_paginated,
    get_lead_details,
    get_queued_leads,
    get_accepted_leads,
    mark_lead_sent,
    mark_lead_accepted,
)
import pipeline_runner

from fastapi import FastAPI, Request, Response, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI(title="Lead Gen Engine — Portal & Action Queue", version="2.0.0")

# Mount static and templates
_static_dir = _root / "static"
_templates_dir = _root / "templates"
_static_dir.mkdir(parents=True, exist_ok=True)
_templates_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(_templates_dir))

COOKIE_NAME = "lead_portal_session"


# ============================================================
# Session Cookie Authentication Engine
# ============================================================

def create_session_token(username: str) -> str:
    """Generate an HMAC-SHA256 signed session cookie token."""
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    sig = hmac.new(
        PORTAL_SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str | None) -> str | None:
    """Validate session token integrity and freshness (max 30 days)."""
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None

    username, timestamp_str, sig = parts
    payload = f"{username}:{timestamp_str}"
    expected_sig = hmac.new(
        PORTAL_SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        ts = int(timestamp_str)
        # 30 day expiration
        if time.time() - ts > 30 * 86400:
            return None
    except ValueError:
        return None

    return username


def get_current_user(request: Request) -> str:
    """Dependency: Require authenticated session. Redirects to /login for HTML requests."""
    token = request.cookies.get(COOKIE_NAME)
    username = verify_session_token(token)

    # Also support HTTP Authorization: Bearer <password> for API consumers
    if not username:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            bearer_key = auth_header.split(" ", 1)[1]
            if secrets.compare_digest(bearer_key, PORTAL_AUTH_PASSWORD):
                return PORTAL_AUTH_USERNAME

    if not username:
        # Check if browser HTML request vs API request
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(status_code=307, headers={"Location": "/login"})
        raise HTTPException(status_code=401, detail="Authentication required")

    return username


# ============================================================
# Auth Views
# ============================================================

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    """Render branded login screen."""
    # If already logged in, redirect to dashboard
    token = request.cookies.get(COOKIE_NAME)
    if verify_session_token(token):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})


@app.post("/login")
def process_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Authenticate user credentials and set secure session cookie."""
    valid_user = secrets.compare_digest(username.strip(), PORTAL_AUTH_USERNAME)
    valid_pass = secrets.compare_digest(password.strip(), PORTAL_AUTH_PASSWORD)

    if not (valid_user and valid_pass):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password. Please try again."},
            status_code=401,
        )

    session_token = create_session_token(username)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=30 * 86400,
        secure=False,  # Set to True automatically when behind HTTPS in Azure
    )
    return response


@app.get("/logout")
def logout():
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ============================================================
# Main Portal & Mobile Queue Views
# ============================================================

@app.get("/", response_class=HTMLResponse)
def view_dashboard(request: Request, user: str = Depends(get_current_user)):
    """Render full desktop & tablet analytics portal."""
    db_engine = "Azure PostgreSQL" if (DATABASE_URL and "postgres" in DATABASE_URL.lower()) else "SQLite WAL"
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"user": user, "db_engine": db_engine},
    )


@app.get("/queue", response_class=HTMLResponse)
def view_standalone_queue(request: Request, user: str = Depends(get_current_user)):
    """Render mobile-optimized action queue for smartphone browser dispatch."""
    return templates.TemplateResponse(request=request, name="queue.html", context={"user": user})


@app.get("/health")
def health():
    """Service health probe."""
    db_engine = "PostgreSQL" if (DATABASE_URL and "postgres" in DATABASE_URL.lower()) else "SQLite"
    return {
        "status": "healthy",
        "db_engine": db_engine,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# Portal REST APIs
# ============================================================

@app.get("/api/stats")
def api_stats(user: str = Depends(get_current_user)):
    """Get consolidated funnel, budget, and follower statistics."""
    conn = get_connection()
    stats = get_pipeline_stats(conn)
    conn.close()
    return stats


@app.get("/api/leads")
def api_leads(
    status: str = Query("ALL"),
    follower_tier: str = Query("ALL"),
    search: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: str = Depends(get_current_user),
):
    """Paginated search & filtering across all leads."""
    conn = get_connection()
    leads, total = get_leads_paginated(
        conn,
        status=status,
        follower_tier=follower_tier,
        search=search,
        limit=limit,
        offset=offset,
    )
    conn.close()
    return {"leads": leads, "total": total, "limit": limit, "offset": offset}


@app.get("/api/leads/{lead_id}")
def api_lead_detail(lead_id: str, user: str = Depends(get_current_user)):
    """Get complete lead record with state transition audit trail."""
    conn = get_connection()
    lead = get_lead_details(conn, lead_id)
    conn.close()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get("/api/queue")
def api_queue(
    limit: int = Query(15, ge=1, le=50),
    follower_tier: str | None = Query(None),
    user: str = Depends(get_current_user),
):
    """Fetch leads ready for dispatch in mobile queue."""
    conn = get_connection()
    leads = get_queued_leads(conn, limit=limit, follower_tier=follower_tier)
    conn.close()
    return leads


@app.get("/api/accepted")
def api_accepted(limit: int = Query(50, ge=1, le=100), user: str = Depends(get_current_user)):
    """Fetch accepted leads for post-connection conversation hub."""
    conn = get_connection()
    leads = get_accepted_leads(conn, limit=limit)
    conn.close()
    return leads


@app.post("/api/leads/{lead_id}/mark-sent")
def api_mark_sent(
    lead_id: str,
    outreach_type: str = Query("blank"),
    user: str = Depends(get_current_user),
):
    """Mark lead as SENT (outreach_type: blank or custom_note)."""
    conn = get_connection()
    try:
        res = mark_lead_sent(conn, lead_id, outreach_type=outreach_type)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@app.post("/api/leads/{lead_id}/mark-accepted")
def api_mark_accepted(lead_id: str, user: str = Depends(get_current_user)):
    """Mark lead as ACCEPTED when connection is approved."""
    conn = get_connection()
    try:
        res = mark_lead_accepted(conn, lead_id)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ============================================================
# UI-Triggered Pipeline Execution
# ============================================================

class PipelineRunRequest(BaseModel):
    stage_id: str
    batch_size: int = 15
    generate_ai_note: bool = False
    follower_filter: str = "ALL"


@app.post("/api/pipeline/run")
def api_pipeline_run(req: PipelineRunRequest, user: str = Depends(get_current_user)):
    """
    Launch an asynchronous pipeline stage execution from the UI Portal:
    - Enforces strict batch size
    - Enforces strict AI opening text generation toggle
    - Enforces follower filtering
    """
    job_id = pipeline_runner.launch_pipeline_job(
        stage_id=req.stage_id,
        batch_size=req.batch_size,
        generate_ai_note=req.generate_ai_note,
        follower_filter=req.follower_filter,
    )
    return {"job_id": job_id, "status": "QUEUED"}


@app.get("/api/pipeline/status/{job_id}")
def api_pipeline_status(job_id: str, user: str = Depends(get_current_user)):
    """Poll execution logs and progress for a running pipeline job."""
    job = pipeline_runner.registry.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    init_db()
    import uvicorn
    print(f"\n  🚀 Lead Gen Portal running at http://{QUEUE_HOST}:{QUEUE_PORT}\n")
    uvicorn.run(app, host=QUEUE_HOST, port=QUEUE_PORT)
