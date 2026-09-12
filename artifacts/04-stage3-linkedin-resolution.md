# Module 04: Stage 3 — Search Engine LinkedIn Profile Resolution

> **Stage Mission:** Resolve candidate personal LinkedIn profile URLs using search engine dorking (Bing Search API on Azure) without querying LinkedIn's internal search UI, bypassing auth-walls and search quotas.

---

## 1. Input & Output Contract

* **Input Query:** Leads with `status = 'ENRICHED'`, non-null `founder_name`, and non-null `business_name`.
* **Output Updates:**
  * If valid match found: `status = 'LINKEDIN_RESOLVED'`, `linkedin_url`, `linkedin_slug`, `resolution_confidence`.
  * If no profile or low confidence: `status = 'NO_LINKEDIN'`.
* **Prerequisites:** Azure Bing Web Search API Key (subsidized by $5,000 credits).

---

## 2. Search Query Formulation & Scoring Heuristics

The engine executes this precise dorking query:
```
site:linkedin.com/in/ "{founder_name}" "{business_name}"
```

### Disambiguation & Match Scoring
When search returns multiple candidate URLs, each result is scored:

| Signal | Point Value | Condition |
|---|---|---|
| **Company Exact Match** | +40 pts | Search snippet contains exact business name |
| **Title Match** | +25 pts | Snippet contains "Founder", "CEO", "Owner", "Partner" |
| **Geographic Match** | +20 pts | Snippet contains target city/state |
| **Slug Match** | +15 pts | URL slug contains founder's first/last name |

**Acceptance Threshold:** Candidate must score $\ge 50$ points to proceed to Stage 4.

---

## 3. Standalone Runner Script (`stages/stage3_resolve.py`)

Run independently via `python -m stages.stage3_resolve --batch-size 50` (supports Serper, Google, and Bing):

```python
import os
import re
import requests
import sqlite3
from config import DB_PATH

BING_SEARCH_API_KEY = os.getenv("AZURE_BING_SEARCH_KEY")
BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"

def score_candidate(snippet: str, title: str, founder_name: str, biz_name: str) -> int:
    score = 0
    text = (title + " " + snippet).lower()
    
    if biz_name.lower() in text:
        score += 40
    if any(role in text for role in ["founder", "ceo", "owner", "partner", "director"]):
        score += 25
    if founder_name.lower().split()[0] in text:
        score += 15
        
    return score

def resolve_linkedin_batch(limit=20, db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    
    cur.execute("SELECT id, founder_name, business_name FROM leads WHERE status = 'ENRICHED' LIMIT ?", (limit,))
    leads = cur.fetchall()
    
    headers = {"Ocp-Apim-Subscription-Key": BING_SEARCH_API_KEY}
    
    for lead_id, founder, biz in leads:
        query = f'site:linkedin.com/in/ "{founder}" "{biz}"'
        params = {"q": query, "count": 5}
        
        try:
            res = requests.get(BING_ENDPOINT, headers=headers, params=params, timeout=6)
            res.raise_for_status()
            data = res.json()
            web_pages = data.get("webPages", {}).get("value", [])
            
            best_url = None
            best_score = 0
            
            for page in web_pages:
                url = page.get("url", "")
                if "linkedin.com/in/" not in url:
                    continue
                    
                score = score_candidate(page.get("snippet", ""), page.get("name", ""), founder, biz)
                if score > best_score:
                    best_score = score
                    best_url = url
                    
            if best_url and best_score >= 50:
                slug_match = re.search(r'linkedin\.com/in/([^/?]+)', best_url)
                slug = slug_match.group(1) if slug_match else ""
                
                cur.execute("""
                    UPDATE leads 
                    SET status = 'LINKEDIN_RESOLVED', linkedin_url = ?, linkedin_slug = ?, 
                        search_dork_query = ?, resolution_confidence = ?
                    WHERE id = ?
                """, (best_url, slug, query, best_score / 100.0, lead_id))
                print(f"✓ Resolved: {founder} -> {best_url} (Score: {best_score})")
            else:
                cur.execute("UPDATE leads SET status = 'NO_LINKEDIN' WHERE id = ?", (lead_id,))
                print(f"✗ No LinkedIn match: {founder} at {biz}")
                
        except Exception as e:
            print(f"Error querying Bing: {e}")
            
        conn.commit()
    conn.close()

if __name__ == "__main__":
    resolve_linkedin_batch(10)
```
