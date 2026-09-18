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

import re
from config import DB_PATH, DATABASE_URL, VALID_TRANSITIONS


# ============================================================
# Schema DDL — Updated with Follower Tiering & Outreach Type
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
    follower_count INTEGER,                   -- Parsed follower count (e.g. 4000, 22000)
    follower_tier TEXT,                       -- 'SWEET_SPOT_500_5K', 'OVER_5K', 'UNDER_500', 'UNKNOWN'
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
    outreach_type TEXT,                       -- 'blank', 'custom_note'
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


try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


PG_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Stage 1
    google_place_id TEXT UNIQUE,
    business_name TEXT NOT NULL,
    formatted_address TEXT,
    city TEXT,
    website_uri TEXT,
    national_phone TEXT,
    search_query TEXT,

    -- Stage 2
    domain TEXT,
    website_status TEXT,
    founder_name TEXT,
    founder_title TEXT,
    extraction_confidence REAL,
    business_summary TEXT,
    general_email TEXT,
    website_copyright_year INTEGER,

    -- Stage 3
    linkedin_url TEXT,
    linkedin_slug TEXT,
    linkedin_headline TEXT,
    founder_profile_details TEXT,
    search_dork_query TEXT,
    search_dork_snippet TEXT,
    resolution_confidence REAL,

    -- Stage 4
    linkedin_connection_count INTEGER,
    follower_count INTEGER,
    follower_tier TEXT,
    has_active_role BOOLEAN,
    last_activity_date DATE,
    last_activity_days_ago INTEGER,
    activity_check_status TEXT,
    activity_gate_signals TEXT,

    -- Stage 5
    personalized_note TEXT,
    note_char_count INTEGER,
    note_generated_at TIMESTAMP,
    ab_variant TEXT DEFAULT 'variant_a',

    -- Stage 6
    queued_at TIMESTAMP,
    sent_at TIMESTAMP,
    outreach_type TEXT,
    connection_accepted_at TIMESTAMP,
    first_reply_at TIMESTAMP,
    rejection_reason TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS api_calls (
    id SERIAL PRIMARY KEY,
    api_name TEXT NOT NULL,
    endpoint TEXT,
    query TEXT,
    results_count INTEGER,
    cost_usd REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id SERIAL PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    triggered_by_stage TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pg_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_pg_leads_city ON leads(city);
CREATE INDEX IF NOT EXISTS idx_pg_leads_domain ON leads(domain);
CREATE INDEX IF NOT EXISTS idx_pg_leads_follower_tier ON leads(follower_tier);
CREATE INDEX IF NOT EXISTS idx_pg_transitions_lead ON state_transitions(lead_id);
"""


def is_postgres(target: str | None = None) -> bool:
    url = target or DATABASE_URL
    return bool(url and "postgres" in url.lower())


class PostgresCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        return row

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()


class PostgresConnectionWrapper:
    """Wraps a psycopg2 connection to expose a sqlite3-compatible interface."""

    def __init__(self, raw_conn):
        self._conn = raw_conn
        self.row_factory = None

    def execute(self, sql: str, params: tuple | list | None = None):
        if sql.strip().upper().startswith("PRAGMA"):
            class DummyCursor:
                def fetchall(self):
                    return []
                def fetchone(self):
                    return None
            return DummyCursor()

        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        pg_sql = sql.replace("?", "%s")
        if params is not None:
            cur.execute(pg_sql, tuple(params))
        else:
            cur.execute(pg_sql)
        return PostgresCursorWrapper(cur)

    def executemany(self, sql: str, params_seq):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        pg_sql = sql.replace("?", "%s")
        cur.executemany(pg_sql, [tuple(p) for p in params_seq])
        return PostgresCursorWrapper(cur)

    def executescript(self, sql_script: str):
        cur = self._conn.cursor()
        cur.execute(sql_script)
        self._conn.commit()
        cur.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


# ============================================================
# Connection Management
# ============================================================

def get_connection(db_path: str | None = None):
    """Get a database connection (Azure PostgreSQL if DATABASE_URL is set, else SQLite WAL)."""
    if is_postgres(db_path):
        if not psycopg2:
            raise RuntimeError(
                "psycopg2 is not installed. Please install psycopg2-binary to connect to PostgreSQL."
            )
        url = db_path if is_postgres(db_path) else DATABASE_URL
        raw_conn = psycopg2.connect(url)
        return PostgresConnectionWrapper(raw_conn)

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
    if is_postgres(db_path):
        conn = get_connection(db_path)
        conn.executescript(PG_SCHEMA_DDL)
        conn.commit()
        print(f"[OK] Azure PostgreSQL Database initialized with schema")
        conn.close()
        return

    conn = get_connection(db_path)
    conn.executescript(SCHEMA_DDL)
    
    # Run migrations for newly added columns on existing databases
    cursor = conn.execute("PRAGMA table_info(leads)")
    existing_cols = {row["name"] for row in cursor.fetchall()}
    if "linkedin_headline" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN linkedin_headline TEXT")
    if "founder_profile_details" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN founder_profile_details TEXT")
    if "follower_count" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN follower_count INTEGER")
    if "follower_tier" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN follower_tier TEXT")
    if "outreach_type" not in existing_cols:
        conn.execute("ALTER TABLE leads ADD COLUMN outreach_type TEXT")
        
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_follower_tier ON leads(follower_tier)")
    conn.commit()

    # Automatically backfill follower tiers on startup if needed
    backfill_follower_tiers(conn)

    conn.close()
    print(f"[OK] Database initialized: {db_path or DB_PATH}")


# ============================================================
# Follower & Audience Parsing and Classification
# ============================================================

def parse_numeric_with_multiplier(raw_str: str, mult: str | None) -> int | None:
    """Parse numeric string with optional K/M multiplier into an integer."""
    cleaned = raw_str.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        val = float(cleaned)
        if mult:
            m = mult.lower()
            if m == "k":
                val *= 1000
            elif m == "m":
                val *= 1000000
        return int(val)
    except Exception:
        return None


def extract_audience_numbers(text: str | None) -> tuple[int | None, int | None, str]:
    """
    Parse follower and connection counts from snippet or profile text.
    Returns: (follower_count, connection_count, follower_tier)
    Tiers:
      - 'SWEET_SPOT_500_5K': 500+ connections up to 5,000 followers
      - 'OVER_5K': > 5,000 followers / connections (Celebrity/Macro accounts to avoid)
      - 'UNDER_500': < 500 connections / followers
      - 'UNKNOWN': No follower or connection data in snippet
    """
    if not text:
        return None, None, "UNKNOWN"

    followers = None
    connections = None

    # Follower match (e.g. "22K followers", "4,500 followers", "93k followers")
    f_match = re.search(r'(\d+(?:[\.,]\d+)?)\s*([kKmM])?\+?\s*followers?', text, re.IGNORECASE)
    if f_match:
        followers = parse_numeric_with_multiplier(f_match.group(1), f_match.group(2))

    # Connection match (e.g. "500+ connections", "388 connections")
    if "500+ connections" in text.lower():
        connections = 500
    else:
        c_match = re.search(r'(\d+(?:[\.,]\d+)?)\s*([kKmM])?\+?\s*connections?', text, re.IGNORECASE)
        if c_match:
            connections = parse_numeric_with_multiplier(c_match.group(1), c_match.group(2))

    # Classify tier
    # Primary signal is followers if specified, otherwise connections
    effective = followers if followers is not None else connections

    if effective is None:
        tier = "UNKNOWN"
    elif effective > 5000:
        tier = "OVER_5K"
    elif effective >= 500:
        tier = "SWEET_SPOT_500_5K"
    else:
        tier = "UNDER_500"

    return followers, connections, tier


def backfill_follower_tiers(conn: sqlite3.Connection) -> dict[str, int]:
    """
    Backfill follower_count and follower_tier for existing leads where follower_tier IS NULL.
    """
    cur = conn.execute(
        "SELECT id, search_dork_snippet, founder_profile_details, linkedin_connection_count "
        "FROM leads WHERE follower_tier IS NULL"
    )
    rows = cur.fetchall()
    if not rows:
        return {"updated": 0}

    updated_count = 0
    tier_counts = {"SWEET_SPOT_500_5K": 0, "OVER_5K": 0, "UNDER_500": 0, "UNKNOWN": 0}

    for row in rows:
        lead_id = row["id"]
        snippet = row["search_dork_snippet"] or ""
        details = row["founder_profile_details"] or ""
        combined = f"{snippet} {details}"
        existing_conn = row["linkedin_connection_count"]

        followers, connections, tier = extract_audience_numbers(combined)
        if existing_conn and connections is None:
            connections = existing_conn
            if followers is None and connections:
                if connections > 5000:
                    tier = "OVER_5K"
                elif connections >= 500:
                    tier = "SWEET_SPOT_500_5K"
                else:
                    tier = "UNDER_500"

        conn.execute(
            """
            UPDATE leads
            SET follower_count = ?,
                linkedin_connection_count = COALESCE(?, linkedin_connection_count),
                follower_tier = ?
            WHERE id = ?
            """,
            (followers, connections, tier, lead_id),
        )
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        updated_count += 1

    conn.commit()
    print(f"[OK] Backfilled follower tiers for {updated_count} leads: {tier_counts}")
    return {"updated": updated_count, "tiers": tier_counts}


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
# Portal Query & Mutation Helpers
# ============================================================

def get_follower_tier_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Get count of leads by follower tier."""
    rows = conn.execute(
        "SELECT COALESCE(follower_tier, 'UNKNOWN') as tier, COUNT(*) as cnt "
        "FROM leads GROUP BY follower_tier"
    ).fetchall()
    counts = {"SWEET_SPOT_500_5K": 0, "OVER_5K": 0, "UNDER_500": 0, "UNKNOWN": 0}
    for row in rows:
        counts[row["tier"]] = row["cnt"]
    return counts


def get_pipeline_stats(conn: sqlite3.Connection) -> dict:
    """Consolidated telemetry and funnel statistics for the portal dashboard."""
    status_counts = get_leads_by_status(conn)
    total = get_total_leads_count(conn)
    tier_counts = get_follower_tier_counts(conn)

    places_spend = get_cumulative_spend(conn, "google_places")
    bing_spend = get_cumulative_spend(conn, "bing_search")
    serper_spend = get_cumulative_spend(conn, "serper_google")
    ai_spend = get_cumulative_spend(conn, "azure_openai")
    total_spend = places_spend + bing_spend + serper_spend + ai_spend

    # Sent counts today and all-time
    sent_all = status_counts.get("SENT", 0)
    accepted_all = status_counts.get("ACCEPTED", 0)
    queued_all = status_counts.get("QUEUED", 0)
    discovered_all = status_counts.get("DISCOVERED", 0)
    enriched_all = status_counts.get("ENRICHED", 0)
    qualified_all = status_counts.get("QUALIFIED_ACTIVE", 0)

    sent_today_row = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE status IN ('SENT', 'ACCEPTED') AND DATE(sent_at) = DATE('now')"
    ).fetchone()
    sent_today = sent_today_row[0] if sent_today_row else 0

    return {
        "total_leads": total,
        "status_counts": status_counts,
        "follower_tiers": tier_counts,
        "queued_count": queued_all,
        "sent_count": sent_all,
        "accepted_count": accepted_all,
        "sent_today": sent_today,
        "spend": {
            "google_places": places_spend,
            "bing_search": bing_spend,
            "serper": serper_spend,
            "azure_openai": ai_spend,
            "total": total_spend,
            "places_budget_cap": 10.0,
        },
    }


def get_leads_paginated(
    conn: sqlite3.Connection,
    status: str | None = None,
    follower_tier: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """
    Query leads with filtering, search, and pagination.
    Returns: (list_of_leads, total_matching_count)
    """
    where_clauses = []
    params = []

    if status and status.upper() != "ALL":
        where_clauses.append("status = ?")
        params.append(status.upper())

    if follower_tier and follower_tier.upper() != "ALL":
        where_clauses.append("follower_tier = ?")
        params.append(follower_tier.upper())

    if search:
        search_pattern = f"%{search.strip()}%"
        where_clauses.append(
            "(business_name LIKE ? OR founder_name LIKE ? OR city LIKE ? OR domain LIKE ?)"
        )
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Count matching
    count_sql = f"SELECT COUNT(*) FROM leads {where_sql}"
    total_matching = conn.execute(count_sql, params).fetchone()[0]

    # Fetch rows
    fetch_sql = f"""
        SELECT id, status, business_name, city, domain, website_status,
               founder_name, founder_title, extraction_confidence,
               linkedin_url, linkedin_headline, follower_count,
               linkedin_connection_count, follower_tier,
               activity_check_status, personalized_note, note_char_count,
               ab_variant, outreach_type, queued_at, sent_at, created_at
        FROM leads
        {where_sql}
        ORDER BY
            CASE status
                WHEN 'QUEUED' THEN 1
                WHEN 'QUALIFIED_ACTIVE' THEN 2
                WHEN 'LINKEDIN_RESOLVED' THEN 3
                WHEN 'ENRICHED' THEN 4
                WHEN 'DISCOVERED' THEN 5
                WHEN 'SENT' THEN 6
                WHEN 'ACCEPTED' THEN 7
                ELSE 8
            END ASC,
            created_at DESC
        LIMIT ? OFFSET ?
    """
    fetch_params = params + [limit, offset]
    rows = conn.execute(fetch_sql, fetch_params).fetchall()
    leads = [dict(row) for row in rows]

    return leads, total_matching


def get_lead_details(conn: sqlite3.Connection, lead_id: str) -> dict | None:
    """Fetch complete lead record and state transition history."""
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        return None

    lead = dict(row)

    # Fetch transitions
    t_rows = conn.execute(
        """
        SELECT from_status, to_status, triggered_by_stage, reason, created_at
        FROM state_transitions
        WHERE lead_id = ?
        ORDER BY created_at ASC
        """,
        (lead_id,),
    ).fetchall()
    lead["transitions"] = [dict(t) for t in t_rows]

    return lead


def get_queued_leads(
    conn: sqlite3.Connection,
    limit: int = 15,
    follower_tier: str | None = None,
) -> list[dict]:
    """Fetch leads ready for dispatch in Stage 6 mobile queue."""
    where = ["status = 'QUEUED'"]
    params = []

    if follower_tier and follower_tier.upper() != "ALL":
        where.append("follower_tier = ?")
        params.append(follower_tier.upper())

    sql = f"""
        SELECT id, business_name, city, domain, founder_name, founder_title,
               linkedin_url, linkedin_headline, follower_count,
               linkedin_connection_count, follower_tier,
               personalized_note, note_char_count, ab_variant,
               business_summary, queued_at
        FROM leads
        WHERE {' AND '.join(where)}
        ORDER BY queued_at ASC, created_at ASC
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_accepted_leads(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Fetch leads who accepted connections for post-connection conversation."""
    sql = """
        SELECT id, business_name, city, domain, founder_name, founder_title,
               linkedin_url, linkedin_headline, follower_count,
               follower_tier, personalized_note, business_summary,
               connection_accepted_at, sent_at
        FROM leads
        WHERE status = 'ACCEPTED'
        ORDER BY connection_accepted_at DESC, sent_at DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(row) for row in rows]


def mark_lead_sent(
    conn: sqlite3.Connection,
    lead_id: str,
    outreach_type: str = "blank",
) -> dict:
    """Mark a QUEUED lead as SENT with outreach type (blank or custom_note)."""
    row = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise ValueError("Lead not found")
    if row["status"] != "QUEUED":
        raise ValueError(f"Lead status is {row['status']}, expected QUEUED")

    now_iso = datetime.now(timezone.utc).isoformat()
    update_lead_status(
        conn,
        lead_id=lead_id,
        from_status="QUEUED",
        to_status="SENT",
        triggered_by="portal_mobile_queue",
        reason=f"manual_dispatch_{outreach_type}",
        sent_at=now_iso,
        outreach_type=outreach_type,
    )
    conn.commit()
    return {"id": lead_id, "status": "SENT", "outreach_type": outreach_type, "sent_at": now_iso}


def mark_lead_accepted(conn: sqlite3.Connection, lead_id: str) -> dict:
    """Mark a SENT lead as ACCEPTED when connection is approved on LinkedIn."""
    row = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise ValueError("Lead not found")
    if row["status"] != "SENT":
        raise ValueError(f"Lead status is {row['status']}, expected SENT")

    now_iso = datetime.now(timezone.utc).isoformat()
    update_lead_status(
        conn,
        lead_id=lead_id,
        from_status="SENT",
        to_status="ACCEPTED",
        triggered_by="portal_mobile_queue",
        reason="connection_accepted_by_lead",
        connection_accepted_at=now_iso,
    )
    conn.commit()
    return {"id": lead_id, "status": "ACCEPTED", "connection_accepted_at": now_iso}


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
