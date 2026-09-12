# Module 03: Stage 2 — Live Website Crawler & Founder Extraction

> **Stage Mission:** Crawl the live target business domain using `curl_cffi` (bypassing basic anti-bot TLS checks), extract leadership/founder names via a multi-tier confidence hierarchy, and prune dead/parked domains.

---

## 1. Input & Output Contract

* **Input Query:** Leads with `status = 'DISCOVERED'` and non-null `website_uri`.
* **Output Updates:** 
  * If valid founder found: `status = 'ENRICHED'`, `founder_name`, `founder_title`, `extraction_confidence`, `business_summary`.
  * If domain fails DNS/SSL or parked: `status = 'DEAD_WEBSITE'`.
  * If site alive but no leader identified: `status = 'NO_LEADERSHIP'`.

---

## 2. Extraction Hierarchy & Confidence Scoring

```
Priority 1: JSON-LD Schema.org (@type: Organization / Person) ──► Confidence: 0.95
Priority 2: Meta author / OpenGraph tags                      ──► Confidence: 0.60
Priority 3: /about, /team regex ("Founded by {Name}", "CEO")   ──► Confidence: 0.70
Priority 4: Footer Copyright ("© 2024 {Name}")                ──► Confidence: 0.50
```

**Quality Threshold:** Combined confidence must be $\ge 0.60$. If lower, mark `NO_LEADERSHIP` to prevent awkward outreach.

---

## 3. Crawl Strategy & Domain Safety

* **Engine:** `curl_cffi.requests` with `impersonate="chrome124"` (JA3/JA4 fingerprint matching).
* **Pages Crawled per Domain:**
  1. Homepage `/`
  2. `/about` or `/about-us`
  3. `/team` or `/leadership`
* **Timeout:** 8 seconds per page (fail fast to maintain pipeline throughput).

---

## 4. Standalone Runner Script (`stages/stage2_enrich.py`)

Run independently via `python -m stages.stage2_enrich --batch-size 50` on pending `DISCOVERED` records:

```python
import re
import json
import sqlite3
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from curl_cffi import requests
from config import DB_PATH

def extract_founder_from_html(html: str) -> tuple[str, str, float]:
    soup = BeautifulSoup(html, "html.parser")
    
    # 1. Schema.org JSON-LD check
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            # Check for founder in Organization
            if isinstance(data, dict):
                founder = data.get("founder") or data.get("foundingPerson")
                if isinstance(founder, dict) and founder.get("name"):
                    return founder["name"].strip(), "Founder", 0.95
                if isinstance(founder, list) and len(founder) > 0 and founder[0].get("name"):
                    return founder[0]["name"].strip(), "Founder", 0.95
        except Exception:
            continue
            
    # 2. Meta tags
    meta_author = soup.find("meta", attrs={"name": re.compile(r"author", re.I)})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"].strip()
        if len(author.split()) in [2, 3]: # Normal human name
            return author, "Owner", 0.60
            
    # 3. Regex on body text
    body_text = soup.get_text(separator=" ", strip=True)
    founder_match = re.search(r'(?:Founded by|Founder & CEO|CEO|Co-founder)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})', body_text)
    if founder_match:
        return founder_match.group(1).strip(), "Founder/CEO", 0.70
        
    return None, None, 0.0

def process_stage2_batch(limit=20, db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    
    cur.execute("SELECT id, website_uri, business_name FROM leads WHERE status = 'DISCOVERED' LIMIT ?", (limit,))
    rows = cur.fetchall()
    
    for lead_id, url, biz_name in rows:
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        
        try:
            # Crawl homepage
            res = requests.get(url, impersonate="chrome124", timeout=8)
            if res.status_code >= 400:
                cur.execute("UPDATE leads SET status = 'DEAD_WEBSITE', domain = ? WHERE id = ?", (domain, lead_id))
                continue
                
            name, title, conf = extract_founder_from_html(res.text)
            
            # If not found on home, try /about
            if not name:
                about_url = f"{parsed.scheme}://{parsed.netloc}/about"
                try:
                    res_about = requests.get(about_url, impersonate="chrome124", timeout=6)
                    if res_about.status_code == 200:
                        name, title, conf = extract_founder_from_html(res_about.text)
                except Exception:
                    pass

            if name and conf >= 0.60:
                cur.execute("""
                    UPDATE leads 
                    SET status = 'ENRICHED', domain = ?, founder_name = ?, founder_title = ?, extraction_confidence = ?
                    WHERE id = ?
                """, (domain, name, title, conf, lead_id))
                print(f"✓ Found Leader: {name} ({title}) for {biz_name} [Conf: {conf}]")
            else:
                cur.execute("UPDATE leads SET status = 'NO_LEADERSHIP', domain = ? WHERE id = ?", (domain, lead_id))
                print(f"✗ No Leader: {biz_name}")
                
        except Exception as e:
            cur.execute("UPDATE leads SET status = 'DEAD_WEBSITE', domain = ? WHERE id = ?", (domain, lead_id))
            print(f"✗ Dead Site: {url} ({e})")
            
        conn.commit()
    conn.close()

if __name__ == "__main__":
    process_stage2_batch(10)
```
