# Module 07: Stage 6 — Mobile Action Queue & Dispatch Workflow

> **Stage Mission:** Serve today's top 10–15 verified leads via a secure, mobile-optimized web endpoint. You open this on your phone, tap the direct LinkedIn profile link, paste the AI-generated note, and hit send in the native LinkedIn app. 
> 
> **Zero Ban Risk:** No Playwright, no browser fingerprinting, no datacenter IPs. Native mobile interactions only.

---

## 1. Input & Output Contract

* **Input Query:** Leads with `status = 'QUEUED'` ordered by `queued_at ASC` limit 15.
* **Output Updates:** When user taps "Mark Sent" on mobile: `status = 'SENT'`, `sent_at = CURRENT_TIMESTAMP`.

---

## 2. Daily 6-Minute Mobile SOP (Standard Operating Procedure)

```
09:00 AM ──► Open http://your-azure-domain/queue on Phone Browser
               │
               ▼
             ┌────────────────────────────────────────────────────────┐
             │ Card 1: Alex Rivera — Founder at Apex Creative         │
             │ [Open LinkedIn Profile] (Deep link to LinkedIn App)    │
             │ Note: "Hey Alex — saw your agency's work..."           │
             │ [One-Tap Copy Note]                                    │
             │ [Mark Sent ✓]                                          │
             └────────────────────────────────────────────────────────┘
               │
               ├── 1. Tap [Open LinkedIn Profile] -> Opens native app
               ├── 2. Tap [One-Tap Copy Note] -> Copies text to clipboard
               ├── 3. In app: Tap Connect -> Add a note -> Paste -> Send
               └── 4. Switch back to browser -> Tap [Mark Sent ✓]
               │
               ▼ (Repeat 10–15 times; ~25 seconds per lead)
09:06 AM ──► 15 Verified Invites Sent. Zero Bot Footprint. Done for the day.
```

---

## 3. Standalone Mobile Web Server (`stages/stage6_queue.py`)

Lightweight FastAPI app with clean mobile HTML and zero complex dependencies. Run via `python -m stages.stage6_queue`:

```python
import sqlite3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from config import DB_PATH

app = FastAPI(title="Lead Mobile Queue")

def get_db():
    return sqlite3.connect(DB_PATH)

@app.get("/queue", response_class=HTMLResponse)
def view_queue():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, business_name, founder_name, founder_title, linkedin_url, personalized_note, note_char_count 
        FROM leads 
        WHERE status = 'QUEUED' 
        LIMIT 15
    """)
    leads = cur.fetchall()
    conn.close()

    cards_html = ""
    for lead in leads:
        lead_id, biz, founder, title, url, note, chars = lead
        cards_html += f"""
        <div style="background:#fff; border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 2px 8px rgba(0,0,0,0.08); font-family:sans-serif;">
            <h3 style="margin:0 0 4px 0; color:#111;">{founder}</h3>
            <p style="margin:0 0 12px 0; color:#666; font-size:14px;">{title} at <b>{biz}</b></p>
            
            <a href="{url}" target="_blank" style="display:inline-block; background:#0077b5; color:#fff; padding:10px 16px; text-decoration:none; border-radius:8px; font-weight:bold; font-size:14px; margin-bottom:12px;">
                Open LinkedIn Profile ↗
            </a>
            
            <div style="background:#f4f4f5; padding:12px; border-radius:8px; font-size:14px; color:#222; margin-bottom:12px;">
                {note}
                <div style="font-size:11px; color:#888; text-align:right; margin-top:4px;">{chars} chars</div>
            </div>
            
            <div style="display:flex; gap:8px;">
                <button onclick="navigator.clipboard.writeText('{note}'); this.innerText='Copied! ✓';" style="flex:1; padding:10px; background:#e4e4e7; border:none; border-radius:8px; font-weight:bold; cursor:pointer;">
                    Copy Note 📋
                </button>
                
                <form action="/mark-sent/{lead_id}" method="post" style="flex:1;">
                    <button type="submit" style="width:100%; padding:10px; background:#10b981; color:#fff; border:none; border-radius:8px; font-weight:bold; cursor:pointer;">
                        Mark Sent ✓
                    </button>
                </form>
            </div>
        </div>
        """

    if not cards_html:
        cards_html = "<div style='text-align:center; padding:40px; color:#666;'>🎉 Queue is empty! No leads queued for today.</div>"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Daily LinkedIn Dispatch</title>
        <style>body {{ background:#f8fafc; padding:16px; margin:0; max-width:480px; margin:0 auto; }}</style>
    </head>
    <body>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
            <h2 style="margin:0; font-family:sans-serif; color:#0f172a;">Daily Action Queue</h2>
            <span style="background:#e0f2fe; color:#0369a1; padding:4px 10px; border-radius:12px; font-weight:bold; font-size:12px;">{len(leads)} Ready</span>
        </div>
        {cards_html}
    </body>
    </html>
    """

@app.post("/mark-sent/{lead_id}")
def mark_sent(lead_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE leads SET status = 'SENT', sent_at = CURRENT_TIMESTAMP WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/queue", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```
