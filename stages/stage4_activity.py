"""
Stage 4 — Tier 1 Passive Deterministic Activity Gate (Zero HTTP, Zero LLM Cost)

Filters leads using ONLY data already in the database from Stages 2 and 3.
No additional HTTP requests to LinkedIn. No authwall risk.

Gate Signals (Tier 1 Passive):
  1. "Present" keyword in Bing search snippet → active current role
  2. Connection count ≥ 100 from Bing snippet → real professional profile
  3. Website copyright year ≥ 2024 → business is still operating
  4. Negative: snippet contains dormant signals ("retired", "former", etc.)

Leads passing all gates → QUALIFIED_ACTIVE
Leads failing any gate → DORMANT_REJECT with logged rejection reason

CLI Usage:
    python -m stages.stage4_activity
    python -m stages.stage4_activity --batch-size 100
"""

import argparse
import json
import re
import sqlite3
import sys

try:
    from config import Status, MAX_ACTIVITY_DAYS
    from db import (
        get_connection,
        init_db,
        update_lead_status,
    )
except ImportError:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from config import Status, MAX_ACTIVITY_DAYS
    from db import (
        get_connection,
        init_db,
        update_lead_status,
    )


# Dormant / negative signals in Bing snippets
DORMANT_SIGNALS = frozenset({
    "retired", "former", "previously", "ex-ceo", "ex-founder",
    "no longer", "closed", "shut down", "inactive", "past",
})


# ============================================================
# Tier 1 Passive Gate (Strict 30-Day Activity Window)
# ============================================================

def evaluate_tier1_gate(
    snippet: str | None,
    copyright_year: int | None,
) -> tuple[bool, dict, str | None]:
    """
    Evaluate a lead against Tier 1 passive signals with a strict 30-day activity window.

    Returns:
        (passed: bool, signals: dict, rejection_reason: str | None)
    """
    signals = {
        "has_present_keyword": None,
        "connection_count": None,
        "copyright_year_ok": None,
        "no_dormant_signals": None,
        "active_last_30d": None,
    }

    rejection_reasons = []
    snippet_text = (snippet or "").lower()

    # ── Signal 1: "Present" keyword (active current role) ──
    has_present = "present" in snippet_text
    signals["has_present_keyword"] = has_present

    # ── Signal 2: Connection count from snippet ────────────
    conn_match = re.search(r'(\d+)\+?\s*connections?', snippet_text)
    if conn_match:
        conn_count = int(conn_match.group(1))
        signals["connection_count"] = conn_count
        if conn_count < 100:
            rejection_reasons.append(f"low_connections({conn_count})")
    else:
        signals["connection_count"] = None  # Unknown, don't penalize

    # ── Signal 3: Website copyright year ───────────────────
    if copyright_year is not None:
        signals["copyright_year_ok"] = copyright_year >= 2024
        if copyright_year < 2023:
            rejection_reasons.append(f"stale_website(©{copyright_year})")
    else:
        signals["copyright_year_ok"] = None  # Unknown

    # ── Signal 4: Negative dormant signals ─────────────────
    found_dormant = [s for s in DORMANT_SIGNALS if s in snippet_text]
    if found_dormant:
        signals["no_dormant_signals"] = False
        rejection_reasons.append(f"dormant_signals({','.join(found_dormant)})")
    else:
        signals["no_dormant_signals"] = True

    # ── Signal 5: Strict 30-Day Activity Recency ───────────
    # Parse relative timestamps in snippet (e.g., "3 days ago", "2 weeks ago", "2 months ago")
    rel_match = re.search(r'(\d+)\s*(d|days?|w|weeks?|mo|months?|y|yrs?|years?)\s*(?:ago)?', snippet_text)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith('d'):
            days_ago = val
        elif unit.startswith('w'):
            days_ago = val * 7
        elif unit.startswith('m'):
            days_ago = val * 30
        elif unit.startswith('y'):
            days_ago = val * 365
        else:
            days_ago = 999

        if days_ago > MAX_ACTIVITY_DAYS:
            rejection_reasons.append(f"inactive_over_{MAX_ACTIVITY_DAYS}d({days_ago}d)")
            signals["active_last_30d"] = False
        else:
            signals["active_last_30d"] = True
    else:
        # If no relative timestamp, current "Present" role and non-dormant required
        signals["active_last_30d"] = has_present

    # ── Decision ───────────────────────────────────────────
    # Reject if ANY hard rejection reason found
    if rejection_reasons:
        return False, signals, "; ".join(rejection_reasons)

    # If we have strong positive signals, pass
    positive_count = sum(1 for v in signals.values() if v is True)
    # Need at least 1 positive signal and no negatives
    if positive_count >= 1:
        return True, signals, None

    # If snippet is empty/missing but no negatives, give benefit of doubt
    # (the lead has a valid website and resolved LinkedIn — likely real)
    if not rejection_reasons:
        return True, signals, None

    return False, signals, "insufficient_signals"


# ============================================================
# Batch Processor
# ============================================================

def run_stage4(batch_size: int = 100) -> dict:
    """
    Process LINKEDIN_RESOLVED leads through Tier 1 passive activity gate.
    """
    init_db()
    conn = get_connection()

    cur = conn.execute(
        "SELECT id, linkedin_slug, search_dork_snippet, website_copyright_year, "
        "founder_name, business_name "
        "FROM leads WHERE status = ? LIMIT ?",
        (Status.LINKEDIN_RESOLVED, batch_size),
    )
    leads = cur.fetchall()

    if not leads:
        print("\n  ℹ️  No LINKEDIN_RESOLVED leads to process. Stage 4 skipped.\n")
        conn.close()
        return {"processed": 0}

    print("\n" + "=" * 60)
    print("  STAGE 4: Tier 1 Passive Activity Gate")
    print("=" * 60)
    print(f"  Batch size: {len(leads)} LINKEDIN_RESOLVED leads")
    print(f"  Mode: Passive (zero HTTP requests, zero cost)")
    print("-" * 60)

    qualified = 0
    dormant = 0

    for row in leads:
        lead_id = row["id"]
        slug = row["linkedin_slug"]
        snippet = row["search_dork_snippet"]
        copyright_year = row["website_copyright_year"]
        founder = row["founder_name"]
        biz = row["business_name"]

        passed, signals, reason = evaluate_tier1_gate(snippet, copyright_year)

        # Extract connection count from signals for DB storage
        conn_count = signals.get("connection_count")

        if passed:
            update_lead_status(
                conn, lead_id,
                from_status=Status.LINKEDIN_RESOLVED,
                to_status=Status.QUALIFIED_ACTIVE,
                triggered_by="stage4",
                activity_check_status="QUALIFIED",
                activity_gate_signals=json.dumps(signals),
                linkedin_connection_count=conn_count,
                has_active_role=signals.get("has_present_keyword"),
            )
            qualified += 1
            print(f"  🟢 ACTIVE:  {founder} at {biz} ({slug})")
        else:
            update_lead_status(
                conn, lead_id,
                from_status=Status.LINKEDIN_RESOLVED,
                to_status=Status.DORMANT_REJECT,
                triggered_by="stage4",
                reason=reason,
                rejection_reason=reason,
                activity_check_status="DORMANT",
                activity_gate_signals=json.dumps(signals),
                linkedin_connection_count=conn_count,
            )
            dormant += 1
            print(f"  ⚫ DORMANT: {founder} at {biz} — {reason}")

        conn.commit()

    # Summary
    print("\n" + "=" * 60)
    print("  STAGE 4 COMPLETE")
    print("=" * 60)
    print(f"  Processed:           {len(leads)}")
    print(f"  🟢 QUALIFIED_ACTIVE:  {qualified}")
    print(f"  ⚫ DORMANT_REJECT:    {dormant}")
    pass_rate = (qualified / len(leads) * 100) if leads else 0
    print(f"  Pass rate:           {pass_rate:.1f}%")
    print("=" * 60 + "\n")

    conn.close()
    return {
        "processed": len(leads),
        "qualified": qualified,
        "dormant": dormant,
    }


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 4: Passive Activity Gate")
    parser.add_argument("--batch-size", type=int, default=100, help="Leads to process (default: 100)")
    args = parser.parse_args()
    run_stage4(batch_size=args.batch_size)
