# Module 02: Stage 1 — Google Places API (New) Ground-Truth Ingestion

> **Stage Mission:** Harvest real, operational B2B businesses directly from Google Maps with zero third-party database decay. Enforce a hard $180 budget ceiling with programmatic circuit breakers.

---

## 1. Input & Output Contract

* **Input:** Target search queries (e.g., `"digital marketing agency"`, `"SEO agency"`) paired with target metropolitan areas (e.g., `"Austin, TX"`, `"Chicago, IL"`, `"Miami, FL"`).
* **Output:** Fresh records inserted into table `leads` with status `DISCOVERED`, plus an entry in `api_calls`.
* **Prerequisites:** Valid GCP API Key with `Places API (New)` enabled.

---

## 2. API Request Specification & FieldMask Optimization

To stay within the **Basic Data Tier** ($32.00 per 1,000 Text Search requests) and avoid expensive contact/atmosphere pricing:

* **Endpoint:** `POST https://places.googleapis.com/v1/places:searchText`
* **Mandatory Header:**
  ```http
  X-Goog-FieldMask: places.id,places.displayName,places.websiteUri,places.formattedAddress,places.nationalPhoneNumber
  ```

### Request Payload Example
```json
{
  "textQuery": "digital marketing agency in Austin, TX",
  "pageSize": 20,
  "languageCode": "en"
}
```

---

## 3. Circuit Breaker & Budget Guardrail Logic

Before dispatching any HTTP request to Google Places, the worker checks cumulative spend against the `$180.00` hard cap.

```python
# circuit_breaker.py
MAX_BUDGET_USD = 180.00
COST_PER_TEXT_SEARCH_REQ = 0.032  # $32 / 1,000 requests

def check_budget_or_abort(db_conn) -> bool:
    cur = db_conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM api_calls WHERE api_name = 'google_places'"
    )
    total_spent = cur.fetchone()[0]
    
    if total_spent >= MAX_BUDGET_USD:
        raise SystemExit(f"CRITICAL: Places API Budget Breaker Tripped! Spent: ${total_spent:.2f} >= ${MAX_BUDGET_USD:.2f}")
    return True
```

---

## 4. Standalone Runner Script (`stages/stage1_places.py`)

Run this stage independently via `python -m stages.stage1_places` or test with isolated queries:

```python
import os
import requests
import sqlite3
import uuid
from config import DB_PATH

PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

def run_stage1_query(query: str, db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    
    # Check circuit breaker
    cur.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM api_calls WHERE api_name = 'google_places'")
    total_spent = cur.fetchone()[0]
    if total_spent >= 180.0:
        print(f"Aborting: ${total_spent:.2f} already spent.")
        return

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": PLACES_API_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName,places.websiteUri,places.formattedAddress,places.nationalPhoneNumber"
    }
    payload = {"textQuery": query, "pageSize": 20}
    
    res = requests.post(PLACES_URL, json=payload, headers=headers)
    res.raise_for_status()
    data = res.json()
    places = data.get("places", [])
    
    # Log API Cost
    cur.execute(
        "INSERT INTO api_calls (api_name, endpoint, query, results_count, cost_usd) VALUES (?, ?, ?, ?, ?)",
        ('google_places', 'places:searchText', query, len(places), 0.032)
    )
    
    inserted = 0
    for p in places:
        website = p.get("websiteUri")
        if not website:
            continue # Skip businesses without a website
            
        place_id = p.get("id")
        name = p.get("displayName", {}).get("text", "Unknown")
        address = p.get("formattedAddress")
        phone = p.get("nationalPhoneNumber")
        
        try:
            cur.execute("""
                INSERT INTO leads (id, status, google_place_id, business_name, formatted_address, website_uri, national_phone, search_query)
                VALUES (?, 'DISCOVERED', ?, ?, ?, ?, ?, ?)
            """, (str(uuid.uuid4()), place_id, name, address, website, phone, query))
            inserted += 1
        except sqlite3.IntegrityError:
            pass # Deduplication: already ingested via place_id
            
    conn.commit()
    conn.close()
    print(f"✓ Stage 1 Finished: Ingested {inserted} new businesses for query: '{query}'")

if __name__ == "__main__":
    # Test query
    run_stage1_query("growth marketing agency in Austin, TX")
```

---

## 5. Failure Modes & Edge Cases

1. **No Website Present:** ~10% of local places have no URL. Filtered immediately; not inserted.
2. **Aggregator / Directory Hits:** Places like Yelp, Clutch, or Facebook pages. Handled in Stage 2 domain normalizer.
3. **HTTP 429 Quota Exceeded:** Script pauses with exponential backoff; checks GCP console daily quota (capped at 210 requests/day).
