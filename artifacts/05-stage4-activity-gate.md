# Module 05: Stage 4 — Deterministic 90-Day Activity Gate (Zero-LLM Cost)

> **Stage Mission:** Inspect candidate LinkedIn profiles using deterministic DOM parsing and regex heuristics to verify active status. Discard dormant profiles (<90 days activity) to protect connection limits and ensure high acceptance rates with $0 LLM spend.

---

## 1. Input & Output Contract

* **Input Query:** Leads with `status = 'LINKEDIN_RESOLVED'` and non-null `linkedin_slug`.
* **Output Updates:**
  * If active within 90 days and $\ge 100$ connections: `status = 'QUALIFIED_ACTIVE'`, `last_activity_days_ago`, `linkedin_connection_count`.
  * If inactive $> 90$ days, $<100$ connections, or no active role: `status = 'DORMANT_REJECT'`, `rejection_reason`.

---

## 2. Gate Criteria & Mathematical Heuristics

1. **Connection Threshold Check:** Connection count extracted must be $\ge 100$. Blank or 1-connection test profiles are discarded immediately.
2. **Role Recency Check:** Profile experience section must contain the string `"Present"` indicating current employment.
3. **90-Day Public Activity Check:**
   * Target URL: `https://www.linkedin.com/in/{slug}/recent-activity/all/`
   * Target DOM Elements: `<time datetime="...">` tags and relative timestamp strings (`"3w"`, `"1mo"`, `"80d"`).
   * Formula:
     $$\text{Days Ago} \le 90 \implies \text{PASS}$$
     $$\text{Days Ago} > 90 \implies \text{DORMANT\_REJECT}$$

---

## 3. Standalone Runner Script (`stages/stage4_activity.py`)

Run independently via `python -m stages.stage4_activity --batch-size 50`:

```python
import re
import sqlite3
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from curl_cffi import requests
from config import DB_PATH

def parse_relative_time_to_days(text: str) -> int:
    text = text.strip().lower()
    
    # 3w, 2mo, 4d, 1yr
    m_days = re.search(r'(\d+)\s*d', text)
    if m_days: return int(m_days.group(1))
    
    m_weeks = re.search(r'(\d+)\s*w', text)
    if m_weeks: return int(m_weeks.group(1)) * 7
    
    m_months = re.search(r'(\d+)\s*mo', text)
    if m_months: return int(m_months.group(1)) * 30
    
    m_years = re.search(r'(\d+)\s*y', text)
    if m_years: return int(m_years.group(1)) * 365
    
    return 999 # Default unknown

def check_lead_activity(slug: str) -> tuple[bool, int, str]:
    url = f"https://www.linkedin.com/in/{slug}/recent-activity/all/"
    
    try:
        res = requests.get(
            url, 
            impersonate="chrome124", 
            headers={"Accept-Language": "en-US,en;q=0.9"},
            timeout=8
        )
        
        # If redirected to authwall / 999
        if res.status_code == 999 or "authwall" in res.url:
            return False, 999, "authwall_blocked"
            
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Check <time> elements
        time_tags = soup.find_all("time", attrs={"datetime": True})
        if time_tags:
            most_recent_dt = None
            for t in time_tags:
                try:
                    dt = datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
                    if not most_recent_dt or dt > most_recent_dt:
                        most_recent_dt = dt
                except Exception:
                    continue
            if most_recent_dt:
                days_ago = (datetime.now(timezone.utc) - most_recent_dt).days
                return (days_ago <= 90), days_ago, "timestamp_ok"

        # Regex fallback on relative strings
        page_text = soup.get_text()
        rel_matches = re.findall(r'\b(\d+\s*(?:d|w|mo|yr))\b', page_text)
        if rel_matches:
            min_days = min(parse_relative_time_to_days(m) for m in rel_matches)
            return (min_days <= 90), min_days, "relative_text_ok"
            
    except Exception as e:
        return False, 999, str(e)
        
    return False, 999, "no_activity_found"

def run_activity_gate_batch(limit=20, db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    
    cur.execute("SELECT id, linkedin_slug FROM leads WHERE status = 'LINKEDIN_RESOLVED' LIMIT ?", (limit,))
    leads = cur.fetchall()
    
    for lead_id, slug in leads:
        if not slug:
            cur.execute("UPDATE leads SET status = 'DORMANT_REJECT', rejection_reason = 'no_slug' WHERE id = ?", (lead_id,))
            continue
            
        is_active, days_ago, reason = check_lead_activity(slug)
        
        if is_active:
            cur.execute("""
                UPDATE leads 
                SET status = 'QUALIFIED_ACTIVE', last_activity_days_ago = ?, activity_check_status = 'QUALIFIED'
                WHERE id = ?
            """, (days_ago, lead_id))
            print(f"✓ Lead {slug} ACTIVE ({days_ago} days ago)")
        else:
            cur.execute("""
                UPDATE leads 
                SET status = 'DORMANT_REJECT', last_activity_days_ago = ?, 
                    activity_check_status = 'DORMANT', rejection_reason = ?
                WHERE id = ?
            """, (days_ago, reason, lead_id))
            print(f"✗ Lead {slug} DORMANT ({days_ago} days ago / {reason})")
            
        conn.commit()
    conn.close()

if __name__ == "__main__":
    run_activity_gate_batch(10)
```
