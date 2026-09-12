"""
Stage 6 — Mobile Action Queue & Pipeline Stats Dashboard

A lightweight FastAPI app serving:
  GET  /queue          — Mobile-optimized lead cards for daily dispatch
  POST /mark-sent/{id} — Mark a lead as SENT after manual LinkedIn send
  GET  /stats          — Real-time pipeline funnel conversion stats
  GET  /health         — Health check endpoint

Protected with HTTP Basic Auth (configurable via .env).

CLI Usage:
    python -m stages.stage6_queue
    python -m stages.stage6_queue --port 8080
"""

import argparse
import secrets
import sqlite3
import sys
from datetime import datetime, timezone

try:
    from config import (
        QUEUE_DAILY_LIMIT,
        QUEUE_HOST,
        QUEUE_PORT,
        QUEUE_AUTH_USERNAME,
        QUEUE_AUTH_PASSWORD,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        get_leads_by_status,
        get_total_leads_count,
        get_cumulative_spend,
    )
except ImportError:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from config import (
        QUEUE_DAILY_LIMIT,
        QUEUE_HOST,
        QUEUE_PORT,
        QUEUE_AUTH_USERNAME,
        QUEUE_AUTH_PASSWORD,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        get_leads_by_status,
        get_total_leads_count,
        get_cumulative_spend,
    )

from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials


app = FastAPI(title="Lead Pipeline — Mobile Action Queue", version="1.0.0")
security = HTTPBasic()


# ============================================================
# Authentication
# ============================================================

def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    """Basic auth verification."""
    correct_user = secrets.compare_digest(credentials.username, QUEUE_AUTH_USERNAME)
    correct_pass = secrets.compare_digest(credentials.password, QUEUE_AUTH_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username


# ============================================================
# HTML Templates
# ============================================================

def render_lead_card(lead: dict) -> str:
    """Render a single lead card for mobile display."""
    lead_id = lead["id"]
    biz = lead["business_name"]
    founder = lead["founder_name"]
    title = lead["founder_title"] or "Founder"
    url = lead["linkedin_url"] or "#"
    note = lead["personalized_note"] or ""
    chars = lead["note_char_count"] or 0
    variant = lead["ab_variant"] or "variant_a"
    city = lead["city"] or ""

    # Escape note for JS clipboard copy (handle quotes)
    note_escaped = note.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", " ")

    variant_badge = (
        '<span style="background:#e0f2fe;color:#0369a1;padding:2px 6px;border-radius:4px;font-size:11px;">A: Note</span>'
        if variant == "variant_a"
        else '<span style="background:#fef3c7;color:#92400e;padding:2px 6px;border-radius:4px;font-size:11px;">B: Blank</span>'
    )

    note_section = ""
    if note:
        note_section = f"""
            <div style="background:#f4f4f5;padding:12px;border-radius:8px;font-size:14px;color:#222;margin-bottom:12px;line-height:1.4;">
                {note}
                <div style="font-size:11px;color:#888;text-align:right;margin-top:4px;">{chars} chars</div>
            </div>
            <button onclick="navigator.clipboard.writeText('{note_escaped}');this.innerText='Copied! ✓';this.style.background='#bbf7d0';"
                    style="width:100%;padding:10px;background:#e4e4e7;border:none;border-radius:8px;font-weight:bold;cursor:pointer;margin-bottom:8px;font-size:14px;">
                📋 Copy Note
            </button>
        """

    return f"""
    <div style="background:#fff;border-radius:12px;padding:16px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,0.08);font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
        <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:8px;">
            <div>
                <h3 style="margin:0 0 2px 0;color:#111;font-size:16px;">{founder}</h3>
                <p style="margin:0;color:#666;font-size:13px;">{title} at <b>{biz}</b></p>
                <p style="margin:2px 0 0 0;color:#999;font-size:12px;">{city}</p>
            </div>
            {variant_badge}
        </div>

        <a href="{url}" target="_blank" rel="noopener"
           style="display:block;background:#0077b5;color:#fff;padding:10px 16px;text-decoration:none;border-radius:8px;font-weight:bold;font-size:14px;margin-bottom:12px;text-align:center;">
            Open LinkedIn Profile ↗
        </a>

        {note_section}

        <form action="/mark-sent/{lead_id}" method="post" style="margin:0;">
            <button type="submit"
                    style="width:100%;padding:12px;background:#10b981;color:#fff;border:none;border-radius:8px;font-weight:bold;cursor:pointer;font-size:15px;">
                ✓ Mark Sent
            </button>
        </form>
    </div>
    """


def render_page(title: str, body: str, back_link: str | None = None) -> str:
    """Render a full HTML page with mobile viewport."""
    nav = ""
    if back_link:
        nav = f'<a href="{back_link}" style="color:#0369a1;text-decoration:none;font-size:14px;">← Back</a>'

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <style>
            body {{
                background: #f8fafc;
                padding: 16px;
                margin: 0;
                max-width: 480px;
                margin: 0 auto;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            }}
        </style>
    </head>
    <body>
        {nav}
        {body}
    </body>
    </html>
    """


# ============================================================
# Routes
# ============================================================

@app.get("/queue", response_class=HTMLResponse)
def view_queue(user: str = Depends(verify_auth)):
    """Mobile action queue: today's leads to send."""
    conn = get_connection()
    cur = conn.execute(
        """
        SELECT id, business_name, founder_name, founder_title,
               linkedin_url, personalized_note, note_char_count,
               ab_variant, city
        FROM leads
        WHERE status = ?
        ORDER BY queued_at ASC
        LIMIT ?
        """,
        (Status.QUEUED, QUEUE_DAILY_LIMIT),
    )
    leads = [dict(row) for row in cur.fetchall()]

    # Count sent today
    sent_today = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE status = ? AND DATE(sent_at) = DATE('now')",
        (Status.SENT,),
    ).fetchone()[0]

    conn.close()

    cards_html = "".join(render_lead_card(lead) for lead in leads)

    if not cards_html:
        cards_html = """
        <div style="text-align:center;padding:60px 20px;color:#666;">
            <div style="font-size:48px;margin-bottom:12px;">🎉</div>
            <h3 style="margin:0 0 8px 0;color:#111;">Queue Empty!</h3>
            <p style="margin:0;">No leads queued. Run the pipeline to generate more.</p>
        </div>
        """

    header = f"""
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h2 style="margin:0;color:#0f172a;font-size:20px;">Daily Action Queue</h2>
        <div style="display:flex;gap:6px;">
            <span style="background:#e0f2fe;color:#0369a1;padding:4px 10px;border-radius:12px;font-weight:bold;font-size:12px;">
                {len(leads)} Ready
            </span>
            <span style="background:#dcfce7;color:#166534;padding:4px 10px;border-radius:12px;font-weight:bold;font-size:12px;">
                {sent_today} Sent Today
            </span>
        </div>
    </div>
    <a href="/stats" style="display:block;text-align:center;color:#0369a1;font-size:13px;margin-bottom:12px;text-decoration:none;">
        📊 View Pipeline Stats
    </a>
    """

    return render_page("Daily LinkedIn Dispatch", header + cards_html)


@app.post("/mark-sent/{lead_id}")
def mark_sent(lead_id: str, user: str = Depends(verify_auth)):
    """Mark a lead as SENT after manual LinkedIn dispatch."""
    conn = get_connection()

    # Verify lead exists and is QUEUED
    row = conn.execute(
        "SELECT status FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Lead not found")

    if row["status"] != Status.QUEUED:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Lead is {row['status']}, not QUEUED")

    conn.execute(
        "UPDATE leads SET status = ?, sent_at = ?, updated_at = ? WHERE id = ?",
        (Status.SENT, datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat(), lead_id),
    )

    # Log transition
    conn.execute(
        "INSERT INTO state_transitions (lead_id, from_status, to_status, triggered_by_stage, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (lead_id, Status.QUEUED, Status.SENT, "stage6", "manual_mobile_dispatch"),
    )

    conn.commit()
    conn.close()
    return RedirectResponse(url="/queue", status_code=303)


@app.get("/stats", response_class=HTMLResponse)
def view_stats(user: str = Depends(verify_auth)):
    """Pipeline funnel stats dashboard."""
    conn = get_connection()
    status_counts = get_leads_by_status(conn)
    total = get_total_leads_count(conn)
    places_spend = get_cumulative_spend(conn, "google_places")
    bing_spend = get_cumulative_spend(conn, "bing_search")
    ai_spend = get_cumulative_spend(conn, "azure_openai")

    conn.close()

    stages = [
        ("DISCOVERED", "🔵", "#dbeafe"),
        ("ENRICHED", "🟢", "#dcfce7"),
        ("DEAD_WEBSITE", "⚫", "#f3f4f6"),
        ("NO_LEADERSHIP", "⚫", "#f3f4f6"),
        ("LINKEDIN_RESOLVED", "🟢", "#dcfce7"),
        ("NO_LINKEDIN", "⚫", "#f3f4f6"),
        ("QUALIFIED_ACTIVE", "🟢", "#dcfce7"),
        ("DORMANT_REJECT", "⚫", "#f3f4f6"),
        ("QUEUED", "🟡", "#fef9c3"),
        ("SENT", "✅", "#d1fae5"),
        ("ACCEPTED", "🏆", "#fef3c7"),
    ]

    rows_html = ""
    for status, icon, bg in stages:
        count = status_counts.get(status, 0)
        if count > 0 or status in ("DISCOVERED", "ENRICHED", "QUEUED", "SENT"):
            pct = (count / total * 100) if total > 0 else 0
            bar_width = min(pct * 2, 100)
            rows_html += f"""
            <div style="display:flex;align-items:center;padding:8px 12px;background:{bg};border-radius:8px;margin-bottom:6px;">
                <span style="width:24px;">{icon}</span>
                <span style="flex:1;font-size:13px;color:#333;">{status}</span>
                <span style="font-weight:bold;font-size:14px;color:#111;min-width:40px;text-align:right;">{count}</span>
                <div style="width:60px;height:8px;background:#e5e7eb;border-radius:4px;margin-left:8px;">
                    <div style="width:{bar_width}%;height:100%;background:#3b82f6;border-radius:4px;"></div>
                </div>
            </div>
            """

    body = f"""
    <h2 style="margin:0 0 16px 0;color:#0f172a;font-size:20px;">📊 Pipeline Stats</h2>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px;">
        <div style="background:#fff;padding:12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.06);text-align:center;">
            <div style="font-size:24px;font-weight:bold;color:#111;">{total}</div>
            <div style="font-size:12px;color:#666;">Total Leads</div>
        </div>
        <div style="background:#fff;padding:12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.06);text-align:center;">
            <div style="font-size:24px;font-weight:bold;color:#10b981;">{status_counts.get('SENT', 0)}</div>
            <div style="font-size:12px;color:#666;">Invites Sent</div>
        </div>
        <div style="background:#fff;padding:12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.06);text-align:center;">
            <div style="font-size:24px;font-weight:bold;color:#f59e0b;">{status_counts.get('QUEUED', 0)}</div>
            <div style="font-size:12px;color:#666;">Ready to Send</div>
        </div>
        <div style="background:#fff;padding:12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.06);text-align:center;">
            <div style="font-size:24px;font-weight:bold;color:#ef4444;">${places_spend + bing_spend + ai_spend:.2f}</div>
            <div style="font-size:12px;color:#666;">Total API Spend</div>
        </div>
    </div>

    <h3 style="margin:0 0 8px 0;color:#374151;font-size:15px;">Funnel Breakdown</h3>
    {rows_html}

    <div style="margin-top:16px;padding:12px;background:#fff;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,0.06);">
        <h4 style="margin:0 0 8px 0;color:#374151;font-size:13px;">API Spend Breakdown</h4>
        <div style="font-size:13px;color:#555;">
            <div>Google Places: ${places_spend:.2f}</div>
            <div>Bing Search: ${bing_spend:.2f}</div>
            <div>Azure OpenAI: ${ai_spend:.2f}</div>
        </div>
    </div>
    """

    return render_page("Pipeline Stats", body, back_link="/queue")


@app.get("/health")
def health_check():
    """Health check endpoint for monitoring."""
    return JSONResponse({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 6: Mobile Action Queue Server")
    parser.add_argument("--host", default=QUEUE_HOST, help=f"Bind host (default: {QUEUE_HOST})")
    parser.add_argument("--port", type=int, default=QUEUE_PORT, help=f"Bind port (default: {QUEUE_PORT})")
    args = parser.parse_args()

    init_db()

    import uvicorn
    print(f"\n  🚀 Mobile Queue running at http://{args.host}:{args.port}/queue\n")
    uvicorn.run(app, host=args.host, port=args.port)
