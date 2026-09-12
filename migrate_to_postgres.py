"""
1-Click Database Migration: SQLite -> Azure Database for PostgreSQL (Flexible Server)

Transfers all records (leads, state transitions, API audit calls) from
local SQLite (data/pipeline.db) to Azure PostgreSQL.

Usage:
    # Uses DATABASE_URL from .env
    .venv/Scripts/python migrate_to_postgres.py

    # Explicit connection string
    .venv/Scripts/python migrate_to_postgres.py --url "postgresql://user:pass@lead-gen-db.postgres.database.azure.com:5432/leadgen?sslmode=require"
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DB_PATH, DATABASE_URL

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


def run_migration(sqlite_path: str, pg_url: str) -> None:
    """Migrate all tables and data from SQLite to PostgreSQL."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("❌ Error: psycopg2-binary is required. Run: pip install psycopg2-binary")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  🚀 1-CLICK SQLITE -> AZURE POSTGRESQL MIGRATION")
    print("=" * 60)
    print(f"  Source (SQLite):  {sqlite_path}")
    print(f"  Target (Postgres): {pg_url.split('@')[-1] if '@' in pg_url else 'configured connection'}")
    print("-" * 60)

    if not Path(sqlite_path).exists():
        print(f"❌ Error: SQLite file {sqlite_path} does not exist.")
        sys.exit(1)

    # 1. Connect to SQLite
    s_conn = sqlite3.connect(sqlite_path)
    s_conn.row_factory = sqlite3.Row

    # 2. Connect to PostgreSQL
    print("  Connecting to Azure PostgreSQL...")
    try:
        pg_conn = psycopg2.connect(pg_url)
        pg_conn.autocommit = False
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

    print("  ✓ Connected successfully!")

    # 3. Create Schema on PostgreSQL
    print("  Creating schema on PostgreSQL...")
    with pg_conn.cursor() as cur:
        cur.execute(PG_SCHEMA_DDL)
    pg_conn.commit()
    print("  ✓ Tables and indices verified.")

    start_time = time.time()

    # 4. Migrate 'leads'
    print("\n  📦 Migrating leads table...")
    s_cur = s_conn.execute("SELECT * FROM leads")
    lead_rows = [dict(r) for r in s_cur.fetchall()]
    print(f"     Found {len(lead_rows)} leads in SQLite.")

    if lead_rows:
        cols = list(lead_rows[0].keys())
        col_names = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f"""
            INSERT INTO leads ({col_names})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                follower_count = EXCLUDED.follower_count,
                follower_tier = EXCLUDED.follower_tier,
                updated_at = EXCLUDED.updated_at
        """
        data_tuples = [[row[c] for c in cols] for row in lead_rows]

        with pg_conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, insert_sql, data_tuples, page_size=200)
        pg_conn.commit()
        print(f"     ✓ {len(lead_rows)} leads successfully upserted to PostgreSQL.")

    # 5. Migrate 'api_calls'
    print("\n  📦 Migrating api_calls table...")
    s_cur = s_conn.execute("SELECT api_name, endpoint, query, results_count, cost_usd, created_at FROM api_calls")
    api_rows = [dict(r) for r in s_cur.fetchall()]
    print(f"     Found {len(api_rows)} API call audit records in SQLite.")

    if api_rows:
        cols = ["api_name", "endpoint", "query", "results_count", "cost_usd", "created_at"]
        col_names = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f"INSERT INTO api_calls ({col_names}) VALUES ({placeholders})"
        data_tuples = [[row[c] for c in cols] for row in api_rows]

        with pg_conn.cursor() as cur:
            # Clear existing to avoid duplicate logs if re-running
            cur.execute("TRUNCATE api_calls RESTART IDENTITY CASCADE")
            psycopg2.extras.execute_batch(cur, insert_sql, data_tuples, page_size=200)
        pg_conn.commit()
        print(f"     ✓ {len(api_rows)} API calls migrated.")

    # 6. Migrate 'state_transitions'
    print("\n  📦 Migrating state_transitions table...")
    s_cur = s_conn.execute("SELECT lead_id, from_status, to_status, triggered_by_stage, reason, created_at FROM state_transitions")
    trans_rows = [dict(r) for r in s_cur.fetchall()]
    print(f"     Found {len(trans_rows)} state transitions in SQLite.")

    if trans_rows:
        cols = ["lead_id", "from_status", "to_status", "triggered_by_stage", "reason", "created_at"]
        col_names = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        insert_sql = f"INSERT INTO state_transitions ({col_names}) VALUES ({placeholders})"
        data_tuples = [[row[c] for c in cols] for row in trans_rows]

        with pg_conn.cursor() as cur:
            cur.execute("TRUNCATE state_transitions RESTART IDENTITY CASCADE")
            psycopg2.extras.execute_batch(cur, insert_sql, data_tuples, page_size=200)
        pg_conn.commit()
        print(f"     ✓ {len(trans_rows)} state transitions migrated.")

    s_conn.close()
    pg_conn.close()

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"  🎉 MIGRATION COMPLETE in {elapsed:.2f}s!")
    print(f"  Total Leads Transferred:       {len(lead_rows):,}")
    print(f"  State Transitions Transferred: {len(trans_rows):,}")
    print(f"  API Audit Logs Transferred:    {len(api_rows):,}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SQLite pipeline data to Azure PostgreSQL")
    parser.add_argument("--url", default=DATABASE_URL, help="PostgreSQL connection string (defaults to DATABASE_URL in .env)")
    parser.add_argument("--sqlite", default=DB_PATH, help=f"SQLite database file (default: {DB_PATH})")
    args = parser.parse_args()

    if not args.url:
        print("❌ Error: No PostgreSQL connection URL provided.")
        print("   Set DATABASE_URL in your .env file or pass --url 'postgresql://...'")
        sys.exit(1)

    run_migration(args.sqlite, args.url)
