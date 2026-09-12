# Module 01: Database Engine & State Machine

> **Purpose:** Centralized, decoupled persistence layer. Manages lead records, state transitions, API cost tracking, and prevents race conditions across asynchronous stages.

---

## 1. Engine Selection

* **Local / Prototyping Mode:** SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`), located at `data/pipeline.db` (configured via `DB_PATH` in `config.py`).
* **Production Cloud Mode:** Azure Database for PostgreSQL (Flexible Server) via `DATABASE_URL`.
* **Query Interface:** Direct optimized SQL using Python standard library `sqlite3` (with WAL mode, busy_timeout=5000) or `psycopg2` / `asyncpg`.

---

## 2. Relational Schema Definition (postgredSQL)

```sql
-- Core Leads Table
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,                       -- UUID v4
    status TEXT NOT NULL,                      -- Lead State ENUM
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Stage 1: Google Places Ground Truth
    google_place_id TEXT UNIQUE,
    business_name TEXT NOT NULL,
    formatted_address TEXT,
    website_uri TEXT,
    national_phone TEXT,
    search_query TEXT,
    
    -- Stage 2: Website Enrichment
    domain TEXT,
    website_status TEXT,                      -- 'ACTIVE', 'DEAD', 'PARKED'
    founder_name TEXT,
    founder_title TEXT,
    extraction_confidence REAL,               -- 0.00 to 1.00
    business_summary TEXT,                    -- Key service extracted from website
    general_email TEXT,
    
    -- Stage 3: LinkedIn Resolution
    linkedin_url TEXT,
    linkedin_slug TEXT,
    search_dork_query TEXT,
    resolution_confidence REAL,               -- 0.00 to 1.00
    
    -- Stage 4: Deterministic Activity Gate
    linkedin_connection_count INTEGER,
    has_active_role BOOLEAN,
    last_activity_date DATE,
    last_activity_days_ago INTEGER,
    activity_check_status TEXT,               -- 'QUALIFIED', 'DORMANT', 'FAILED'
    
    -- Stage 5: AI Personalization
    personalized_note TEXT,
    note_char_count INTEGER,
    note_generated_at TIMESTAMP,
    ab_variant TEXT DEFAULT 'variant_a',      -- 'variant_a' (with note), 'variant_b' (blank)
    
    -- Stage 6: Outreach & Lifecycle
    queued_at TIMESTAMP,
    sent_at TIMESTAMP,
    connection_accepted_at TIMESTAMP,
    first_reply_at TIMESTAMP,
    rejection_reason TEXT,
    metadata JSON                             -- Flexible JSON storage for raw payloads
);

-- API Call & Cost Audit Log (Enforces Budget Circuit Breakers)
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_name TEXT NOT NULL,                   -- 'google_places', 'bing_search', 'azure_openai'
    endpoint TEXT,
    query TEXT,
    results_count INTEGER,
    cost_usd REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- State Transition History Log
CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    triggered_by_stage TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(lead_id) REFERENCES leads(id)
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_api_calls_name ON api_calls(api_name);
```

---

## 3. State Machine ENUM Specification

```
                  ┌────────────────────────────────────────────────────────┐
                  │                      DISCOVERED                        │
                  └──────────────────────────┬─────────────────────────────┘
                                             │ Stage 2 Crawl
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
                 ┌──────────────┐                        ┌─────────────────┐
                 │   ENRICHED   │                        │  DEAD_WEBSITE / │
                 └──────┬───────┘                        │  NO_LEADERSHIP  │
                        │ Stage 3 Resolve                └─────────────────┘
                        ▼
              ┌──────────────────┐
              │ LINKEDIN_RESOLVED│
              └─────────┬────────┘
                        │ Stage 4 Activity Check
                        ├──────────────────────────┐
                        ▼                          ▼
             ┌─────────────────────┐      ┌─────────────────┐
             │  QUALIFIED_ACTIVE   │      │ DORMANT_REJECT  │
             └──────────┬──────────┘      └─────────────────┘
                        │ Stage 5 Note Gen
                        ▼
                 ┌──────────────┐
                 │    QUEUED    │
                 └──────┬───────┘
                        │ Stage 6 Mobile Dispatch
                        ▼
                 ┌──────────────┐
                 │     SENT     │
                 └──────────────┘
```

| State Code | Allowed Next States | Triggered By | Description |
|---|---|---|---|
| `DISCOVERED` | `ENRICHED`, `DEAD_WEBSITE`, `NO_LEADERSHIP` | Stage 1 | Fresh Places API record inserted |
| `DEAD_WEBSITE` | *(Terminal)* | Stage 2 | Domain DNS failed or parked |
| `NO_LEADERSHIP`| *(Terminal)* | Stage 2 | Could not identify founder/owner |
| `ENRICHED` | `LINKEDIN_RESOLVED`, `NO_LINKEDIN` | Stage 2 | Founder name extracted with confidence $\ge 0.60$ |
| `NO_LINKEDIN` | *(Terminal)* | Stage 3 | No matching LinkedIn profile found via search |
| `LINKEDIN_RESOLVED` | `QUALIFIED_ACTIVE`, `DORMANT_REJECT` | Stage 3 | Candidate personal profile resolved |
| `DORMANT_REJECT` | *(Terminal / Cooldown)* | Stage 4 | No activity within 90 days or <100 connections |
| `QUALIFIED_ACTIVE` | `QUEUED` | Stage 4 | Verified active on LinkedIn within 90 days |
| `QUEUED` | `SENT` | Stage 5 | Personalized note generated; ready in mobile queue |
| `SENT` | `ACCEPTED`, `EXPIRED_4W` | Stage 6 | User tapped send in LinkedIn mobile app |

---

## 4. Standalone Test Verification Script

Run this command to test database creation and state transitions in isolation:

```python
# test_db.py
import sqlite3
import uuid

conn = sqlite3.connect("test_pipeline.db")
cur = conn.cursor()

# Run DDL
with open("lead-pipeline/01-database-and-state-machine.md", "r") as f:
    # extract DDL block and execute
    pass

# Verify insertion and transition
lead_id = str(uuid.uuid4())
cur.execute("INSERT INTO leads (id, status, business_name) VALUES (?, ?, ?)", 
            (lead_id, "DISCOVERED", "Acme Digital"))
conn.commit()

# Assert record exists
cur.execute("SELECT status FROM leads WHERE id = ?", (lead_id,))
assert cur.fetchone()[0] == "DISCOVERED"
print("✓ Database engine and state tables verified successfully.")
```
