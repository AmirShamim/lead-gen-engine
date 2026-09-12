"""
Lead Pipeline — Database Engine & State Machine

Manages SQLite WAL (or PostgreSQL) connections, schema initialization,
audit logging, and state transition enforcement.

Usage:
    from db import get_connection, init_db
    init_db()                            # Create tables if not exist
    conn = get_connection()              # Get a connection with WAL mode
    count = get_total_leads_count(conn)  # Check current lead inventory
"""

import sqlite3
import uuid
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import DB_PATH, VALID_TRANSITIONS


# ============================================================
# Schema DDL — Updated with Tier 1 passive gate fields
# ============================================================

SCHEMA_DDL = """
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
    city TEXT,                                 -- Extracted metro (e.g. "Austin, TX")
    website_uri TEXT,
    national_phone TEXT,
    search_query TEXT,

    -- Stage 2: Website Enrichment
    domain TEXT,
    website_status TEXT,                       -- 'ACTIVE', 'DEAD', 'PARKED'
    founder_name TEXT,
    founder_title TEXT,
    extraction_confidence REAL,               -- 0.00 to 1.00
    business_summary TEXT,                     -- Agency tagline or meta description
    general_email TEXT,
    website_copyright_year INTEGER,           -- For Tier 1 passive activity gate

    -- Stage 3: LinkedIn Resolution
    linkedin_url TEXT,
    linkedin_slug TEXT,
    linkedin_headline TEXT,                   -- Exact LinkedIn professional headline
    founder_profile_details TEXT,             -- JSON: connections, location, history, seniority
    search_dork_query TEXT,
    search_dork_snippet TEXT,                 -- Bing snippet for Tier 1 passive gate
    resolution_confidence REAL,               -- 0.00 to 1.00

    -- Stage 4: Deterministic Activity Gate
    linkedin_connection_count INTEGER,
    has_active_role BOOLEAN,
    last_activity_date DATE,
    last_activity_days_ago INTEGER,
    activity_check_status TEXT,               -- 'QUALIFIED', 'DORMANT', 'FAILED'
    activity_gate_signals TEXT,               -- JSON: which Tier 1 signals passed/failed

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
    metadata TEXT                              -- Flexible JSON storage for raw payloads
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

-- Performance indices
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_leads_city ON leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_place_id ON leads(google_place_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_name ON api_calls(api_name);
CREATE INDEX IF NOT EXISTS idx_transitions_lead ON state_transitions(lead_id);
"""


# ============================================================
# Connection Management
# ============================================================

def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and row factory enabled."""
    target_path = Path(db_path or DB_PATH)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | None = None) -> None:
    """Initialize the database schema. Safe to call multiple times (idempotent)."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_DDL)
    
    # Run migrations for newly added columns on existing databases
    cursor = conn.execute("PRAGMA table_info(leads)")
    existing_cols = {row["name"] for row in cursor.fetchall()}
    if "linkedin_headline" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN linkedin_headline TEXT")
    if "founder_profile_details" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN founder_profile_details TEXT")
        
    conn.commit()
    conn.close()
    print(f"[OK] Database initialized: {db_path or DB_PATH}")


# ============================================================
# Lead Count & Budget Queries
# ============================================================

def get_total_leads_count(conn: sqlite3.Connection) -> int:
    """Total number of leads across all statuses."""
    return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]


def get_leads_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Count of leads grouped by status. Returns {status: count}."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status ORDER BY cnt DESC"
    ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


def get_leads_count_for_city(conn: sqlite3.Connection, city: str) -> int:
    """Number of leads already ingested for a specific city."""
    return conn.execute(
        "SELECT COUNT(*) FROM leads WHERE city = ?", (city,)
    ).fetchone()[0]


def get_cumulative_spend(conn: sqlite3.Connection, api_name: str) -> float:
    """Total USD spent on a specific API across all time."""
    return conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM api_calls WHERE api_name = ?",
        (api_name,)
    ).fetchone()[0]


def get_api_request_count(conn: sqlite3.Connection, api_name: str) -> int:
    """Total number of API requests made for a specific API."""
    return conn.execute(
        "SELECT COUNT(*) FROM api_calls WHERE api_name = ?", (api_name,)
    ).fetchone()[0]


# ============================================================
# Record Insertion & Updates
# ============================================================

def generate_lead_id() -> str:
    """Generate a new UUID v4 for a lead record."""
    return str(uuid.uuid4())


def record_api_call(
    conn: sqlite3.Connection,
    api_name: str,
    endpoint: str,
    query: str,
    results_count: int,
    cost_usd: float,
) -> None:
    """Log an API call to the audit trail."""
    conn.execute(
        "INSERT INTO api_calls (api_name, endpoint, query, results_count, cost_usd) "
        "VALUES (?, ?, ?, ?, ?)",
        (api_name, endpoint, query, results_count, cost_usd),
    )


def record_transition(
    conn: sqlite3.Connection,
    lead_id: str,
    from_status: str,
    to_status: str,
    triggered_by: str,
    reason: str | None = None,
) -> None:
    """Log a state transition in the audit trail and validate transition is legal."""
    # Validate transition
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Invalid state transition: {from_status} → {to_status}. "
            f"Allowed: {allowed}"
        )

    conn.execute(
        "INSERT INTO state_transitions "
        "(lead_id, from_status, to_status, triggered_by_stage, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (lead_id, from_status, to_status, triggered_by, reason),
    )


def update_lead_status(
    conn: sqlite3.Connection,
    lead_id: str,
    from_status: str,
    to_status: str,
    triggered_by: str,
    reason: str | None = None,
    **extra_fields,
) -> None:
    """
    Update a lead's status and any additional fields atomically.
    Records the transition in the audit log.
    """
    record_transition(conn, lead_id, from_status, to_status, triggered_by, reason)

    # Build dynamic UPDATE with extra fields
    set_clauses = ["status = ?", "updated_at = ?"]
    params = [to_status, datetime.now(timezone.utc).isoformat()]

    for field, value in extra_fields.items():
        set_clauses.append(f"{field} = ?")
        params.append(value)

    params.append(lead_id)
    sql = f"UPDATE leads SET {', '.join(set_clauses)} WHERE id = ?"
    conn.execute(sql, params)


# ============================================================
# Funnel Summary
# ============================================================

def print_funnel_summary(conn: sqlite3.Connection) -> None:
    """Print a formatted funnel summary to console."""
    status_counts = get_leads_by_status(conn)
    total = get_total_leads_count(conn)
    spend = get_cumulative_spend(conn, "google_places")

    print("\n" + "=" * 60)
    print("  PIPELINE FUNNEL SUMMARY")
    print("=" * 60)
    print(f"  Total Leads in Database: {total}")
    print(f"  Google Places API Spend: ${spend:.2f}")
    print("-" * 60)

    # Ordered display
    display_order = [
        ("DISCOVERED", "🔵"),
        ("ENRICHED", "🟢"),
        ("DEAD_WEBSITE", "⚫"),
        ("NO_LEADERSHIP", "⚫"),
        ("LINKEDIN_RESOLVED", "🟢"),
        ("NO_LINKEDIN", "⚫"),
        ("QUALIFIED_ACTIVE", "🟢"),
        ("DORMANT_REJECT", "⚫"),
        ("QUEUED", "🟡"),
        ("SENT", "✅"),
        ("ACCEPTED", "🏆"),
    ]

    for status, icon in display_order:
        count = status_counts.get(status, 0)
        if count > 0:
            bar = "#" * min(count // 5, 40)
            try:
                print(f"  {icon} {status:<22} {count:>5}  {bar}")
            except UnicodeEncodeError:
                print(f"  [*] {status:<22} {count:>5}  {bar}")

    print("=" * 60 + "\n")


# ============================================================
# CLI: Direct Schema Test
# ============================================================

if __name__ == "__main__":
    init_db()
    conn = get_connection()

    # Verify tables exist
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    print(f"✓ Tables created: {[t['name'] for t in tables]}")

    # Verify column count on leads table
    cols = conn.execute("PRAGMA table_info(leads)").fetchall()
    print(f"✓ Leads table columns: {len(cols)}")

    # Test insert + transition
    test_id = generate_lead_id()
    conn.execute(
        "INSERT INTO leads (id, status, business_name, city) VALUES (?, ?, ?, ?)",
        (test_id, "DISCOVERED", "Test Agency LLC", "Austin, TX"),
    )
    conn.commit()

    count = get_total_leads_count(conn)
    print(f"✓ Test lead inserted. Total leads: {count}")

    # Clean up test data
    conn.execute("DELETE FROM leads WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()
    print("✓ Database engine verified successfully.")
