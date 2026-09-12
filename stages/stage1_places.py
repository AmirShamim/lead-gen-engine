"""
Stage 1 — Smart Google Places API (New) Ground-Truth Ingestion

Harvests real, operational B2B businesses from Google Maps with:
- Global lifetime database cap (default: 2,000 leads)
- Per-city quota (default: 150 per metro)
- Per-run API request cap (default: 150 requests)
- Cumulative spend circuit breaker (default: $10.00)
- Domain blocklist filter (social media, directories, aggregators)
- Strict website filter (drop leads without valid HTTP website)
- Deduplication via google_place_id UNIQUE constraint

CLI Usage:
    # Default: 12 cities × 4 queries × up to 150/city, 2000 global cap
    python -m stages.stage1_places

    # Custom cities and queries
    python -m stages.stage1_places --cities "Austin, TX" "Miami, FL" --per-city 100

    # Expand the global cap for a second run
    python -m stages.stage1_places --max-total 3000 --cities "Denver, CO" "Seattle, WA"

    # Dry run (check what would be fetched, no API calls)
    python -m stages.stage1_places --dry-run
"""

import argparse
import sqlite3
import sys
import time
from urllib.parse import urlparse

import requests

# Allow running as both `python -m stages.stage1_places` and `python stages/stage1_places.py`
try:
    from config import (
        GOOGLE_PLACES_API_KEY,
        PLACES_ENDPOINT,
        PLACES_FIELDMASK,
        PLACES_BUDGET_HARD_CAP_USD,
        PLACES_COST_PER_REQUEST,
        PLACES_MAX_REQUESTS_PER_RUN,
        PLACES_GLOBAL_LEAD_CAP,
        PLACES_PER_CITY_TARGET,
        PLACES_PAGE_SIZE,
        DEFAULT_CITIES,
        DEFAULT_QUERIES,
        DOMAIN_BLOCKLIST,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        get_total_leads_count,
        get_leads_count_for_city,
        get_cumulative_spend,
        record_api_call,
        generate_lead_id,
        print_funnel_summary,
    )
except ImportError:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from config import (
        GOOGLE_PLACES_API_KEY,
        PLACES_ENDPOINT,
        PLACES_FIELDMASK,
        PLACES_BUDGET_HARD_CAP_USD,
        PLACES_COST_PER_REQUEST,
        PLACES_MAX_REQUESTS_PER_RUN,
        PLACES_GLOBAL_LEAD_CAP,
        PLACES_PER_CITY_TARGET,
        PLACES_PAGE_SIZE,
        DEFAULT_CITIES,
        DEFAULT_QUERIES,
        DOMAIN_BLOCKLIST,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        get_total_leads_count,
        get_leads_count_for_city,
        get_cumulative_spend,
        record_api_call,
        generate_lead_id,
        print_funnel_summary,
    )


# ============================================================
# Website Validation
# ============================================================

def is_valid_business_website(uri: str | None) -> bool:
    """
    Reject leads without a real business website.
    Filters: None, empty strings, social media, directories, aggregators.
    """
    if not uri or not uri.startswith("http"):
        return False

    try:
        parsed = urlparse(uri)
        domain = parsed.netloc.replace("www.", "").lower()
    except Exception:
        return False

    if not domain or "." not in domain:
        return False

    # Check against blocklist
    # Match both exact and subdomain (e.g., "m.facebook.com")
    for blocked in DOMAIN_BLOCKLIST:
        if domain == blocked or domain.endswith("." + blocked):
            return False

    return True


# ============================================================
# Pre-Flight Safety Checks
# ============================================================

def preflight_check(
    conn: sqlite3.Connection,
    max_total: int,
    request_count: int,
    max_requests: int,
) -> tuple[bool, str]:
    """
    Triple-layer safety check before every API request.
    Returns (can_proceed, reason).
    """
    # Layer 1: Global lead cap
    total_leads = get_total_leads_count(conn)
    if total_leads >= max_total:
        return False, (
            f"GLOBAL CAP REACHED: {total_leads:,}/{max_total:,} leads in database. "
            f"Run with --max-total {max_total + 500} to expand."
        )

    # Layer 2: Cumulative spend
    total_spent = get_cumulative_spend(conn, "google_places")
    if total_spent >= PLACES_BUDGET_HARD_CAP_USD:
        return False, (
            f"BUDGET CAP REACHED: ${total_spent:.2f} >= "
            f"${PLACES_BUDGET_HARD_CAP_USD:.2f} hard cap."
        )

    # Layer 3: Per-run request cap
    if request_count >= max_requests:
        return False, (
            f"REQUEST CAP REACHED: {request_count}/{max_requests} API requests this run."
        )

    return True, "OK"


# ============================================================
# Core Ingestion Logic
# ============================================================

def fetch_places_page(
    query: str,
    page_token: str | None = None,
) -> tuple[list[dict], str | None]:
    """
    Make a single Google Places API (New) Text Search request with retry for token propagation.
    Returns (places_list, next_page_token_or_None).
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": PLACES_FIELDMASK,
    }

    payload = {
        "textQuery": query,
        "pageSize": PLACES_PAGE_SIZE,
        "languageCode": "en",
    }
    if page_token:
        payload["pageToken"] = page_token

    for attempt in range(2):
        try:
            response = requests.post(PLACES_ENDPOINT, json=payload, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            places = data.get("places", [])
            next_token = data.get("nextPageToken")
            return places, next_token
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 500 and attempt < 1:
                time.sleep(2.0)
                continue
            raise

    return [], None


def insert_lead_from_place(
    conn: sqlite3.Connection,
    place: dict,
    search_query: str,
    city: str,
) -> bool:
    """
    Insert a single lead from a Places API result.
    Returns True if inserted, False if duplicate or filtered.
    """
    website = place.get("websiteUri")
    if not is_valid_business_website(website):
        return False

    place_id = place.get("id")
    if not place_id:
        return False

    # Extract normalized domain for domain-level deduplication across cities
    try:
        parsed = urlparse(website)
        domain = parsed.netloc.replace("www.", "").lower()
    except Exception:
        domain = None

    if domain:
        existing = conn.execute("SELECT id FROM leads WHERE domain = ? LIMIT 1", (domain,)).fetchone()
        if existing:
            return False

    name = place.get("displayName", {}).get("text", "Unknown")
    address = place.get("formattedAddress")
    phone = place.get("nationalPhoneNumber")

    try:
        conn.execute(
            """
            INSERT INTO leads (
                id, status, google_place_id, business_name,
                formatted_address, city, website_uri, domain,
                national_phone, search_query
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generate_lead_id(),
                Status.DISCOVERED,
                place_id,
                name,
                address,
                city,
                website,
                domain,
                phone,
                search_query,
            ),
        )
        return True
    except sqlite3.IntegrityError:
        # Deduplication: google_place_id already exists
        return False


# ============================================================
# Main Stage 1 Runner
# ============================================================

def run_stage1(
    cities: list[str] | None = None,
    queries: list[str] | None = None,
    per_city: int | None = None,
    max_total: int | None = None,
    max_requests: int | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Run Stage 1: Google Places API ingestion.

    Returns summary dict with counts for reporting.
    """
    cities = cities or DEFAULT_CITIES
    queries = queries or DEFAULT_QUERIES
    per_city = per_city or PLACES_PER_CITY_TARGET
    max_total = max_total or PLACES_GLOBAL_LEAD_CAP
    max_requests = max_requests or PLACES_MAX_REQUESTS_PER_RUN

    # Validate API key
    if not GOOGLE_PLACES_API_KEY:
        print("ERROR: GOOGLE_PLACES_API_KEY not set. Add it to .env file.")
        return {"error": "no_api_key"}

    # Initialize database
    init_db()
    conn = get_connection()

    # Pre-run summary
    existing_total = get_total_leads_count(conn)
    remaining_global = max_total - existing_total
    existing_spend = get_cumulative_spend(conn, "google_places")

    print("\n" + "=" * 60)
    print("  STAGE 1: Google Places API Ingestion")
    print("=" * 60)
    print(f"  Existing leads in DB:    {existing_total:,}")
    print(f"  Global cap:              {max_total:,}")
    print(f"  Remaining quota:         {max(0, remaining_global):,}")
    print(f"  Per-city target:         {per_city}")
    print(f"  Cities to query:         {len(cities)}")
    print(f"  Query angles per city:   {len(queries)}")
    print(f"  Max API requests:        {max_requests}")
    print(f"  Cumulative spend:        ${existing_spend:.2f}")
    print(f"  Budget hard cap:         ${PLACES_BUDGET_HARD_CAP_USD:.2f}")
    if dry_run:
        print("  MODE:                    DRY RUN (no API calls)")
    print("-" * 60)

    if remaining_global <= 0:
        print(
            f"\n⛔ GLOBAL CAP ALREADY REACHED: {existing_total:,}/{max_total:,} leads. "
            f"Run with --max-total {max_total + 500} to expand.\n"
        )
        conn.close()
        return {"existing": existing_total, "inserted": 0, "requests": 0}

    if dry_run:
        total_potential = len(cities) * len(queries) * PLACES_PAGE_SIZE * 3
        print(f"\n  Max potential profiles: {total_potential:,}")
        print(f"  Max API requests:       {len(cities) * len(queries) * 3}")
        print(f"  Max cost:               ${len(cities) * len(queries) * 3 * PLACES_COST_PER_REQUEST:.2f}")
        print("\n  Run without --dry-run to execute.\n")
        conn.close()
        return {"dry_run": True}

    # Execution
    request_count = 0
    total_inserted = 0
    total_filtered = 0
    total_duplicates = 0
    halted = False
    halt_reason = ""

    for city in cities:
        if halted:
            break

        city_existing = get_leads_count_for_city(conn, city)
        city_inserted = 0
        city_target = max(0, per_city - city_existing)

        if city_target <= 0:
            print(f"  ⏭  {city}: already at quota ({city_existing}/{per_city})")
            continue

        print(f"\n  📍 {city} (existing: {city_existing}, target: {city_target})")

        for query_template in queries:
            if city_inserted >= city_target or halted:
                break

            full_query = f"{query_template} in {city}"
            page_token = None

            for page_num in range(3):  # Max 3 pages per query (60 results)
                if city_inserted >= city_target or halted:
                    break

                # Triple-layer safety check
                can_proceed, reason = preflight_check(
                    conn, max_total, request_count, max_requests
                )
                if not can_proceed:
                    print(f"\n  ⛔ {reason}")
                    halted = True
                    halt_reason = reason
                    break

                try:
                    places, page_token = fetch_places_page(full_query, page_token)
                    request_count += 1

                    # Log API cost
                    record_api_call(
                        conn,
                        api_name="google_places",
                        endpoint="places:searchText",
                        query=full_query,
                        results_count=len(places),
                        cost_usd=PLACES_COST_PER_REQUEST,
                    )

                    page_inserted = 0
                    page_filtered = 0
                    page_dupes = 0

                    for place in places:
                        if city_inserted >= city_target:
                            break
                        if total_inserted >= (max_total - existing_total):
                            halted = True
                            halt_reason = "Global cap reached during insertion"
                            break

                        was_inserted = insert_lead_from_place(
                            conn, place, full_query, city
                        )

                        if was_inserted:
                            page_inserted += 1
                            city_inserted += 1
                            total_inserted += 1
                        elif is_valid_business_website(place.get("websiteUri")):
                            page_dupes += 1
                            total_duplicates += 1
                        else:
                            page_filtered += 1
                            total_filtered += 1

                    conn.commit()
                    print(
                        f"     Page {page_num + 1}: {len(places)} results → "
                        f"{page_inserted} new, {page_dupes} dupes, "
                        f"{page_filtered} filtered  [{full_query}]"
                    )

                    if not page_token:
                        break  # No more pages for this query

                    # Respectful delay between pages (allows Google Places token propagation)
                    time.sleep(1.5)

                except requests.exceptions.HTTPError as e:
                    if e.response and e.response.status_code == 429:
                        print(f"     ⚠️  Rate limited (HTTP 429). Pausing 30s...")
                        time.sleep(30)
                    else:
                        print(f"     ❌ API error: {e}")
                        break
                except requests.exceptions.RequestException as e:
                    print(f"     ❌ Network error: {e}")
                    break

        print(f"  ✓  {city}: {city_inserted} new leads ingested")

    # Final summary
    final_total = get_total_leads_count(conn)
    final_spend = get_cumulative_spend(conn, "google_places")

    print("\n" + "=" * 60)
    print("  STAGE 1 COMPLETE")
    print("=" * 60)
    print(f"  New leads inserted:      {total_inserted:,}")
    print(f"  Filtered (no website):   {total_filtered:,}")
    print(f"  Duplicates skipped:      {total_duplicates:,}")
    print(f"  API requests made:       {request_count}")
    print(f"  Total spend this run:    ${request_count * PLACES_COST_PER_REQUEST:.2f}")
    print(f"  Cumulative spend:        ${final_spend:.2f}")
    print(f"  Total leads in DB:       {final_total:,}/{max_total:,}")
    if halted:
        print(f"  Halt reason:             {halt_reason}")
    print("=" * 60 + "\n")

    conn.close()

    return {
        "inserted": total_inserted,
        "filtered": total_filtered,
        "duplicates": total_duplicates,
        "requests": request_count,
        "spend": request_count * PLACES_COST_PER_REQUEST,
        "total_in_db": final_total,
    }


# ============================================================
# CLI Entry Point
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 1: Google Places API Ingestion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m stages.stage1_places
  python -m stages.stage1_places --cities "Austin, TX" "Miami, FL" --per-city 100
  python -m stages.stage1_places --max-total 3000 --queries "SaaS marketing agency"
  python -m stages.stage1_places --dry-run
        """,
    )
    parser.add_argument(
        "--cities",
        nargs="+",
        default=None,
        help="Target cities (e.g., 'Austin, TX' 'Miami, FL'). Default: 12 US metros.",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="Search query templates (e.g., 'growth agency' 'SEO firm'). Default: 4 angles.",
    )
    parser.add_argument(
        "--per-city",
        type=int,
        default=None,
        help=f"Max leads per city (default: {PLACES_PER_CITY_TARGET}).",
    )
    parser.add_argument(
        "--max-total",
        type=int,
        default=None,
        help=f"Global lifetime cap across all runs (default: {PLACES_GLOBAL_LEAD_CAP}).",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help=f"Max API requests this run (default: {PLACES_MAX_REQUESTS_PER_RUN}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be fetched without making API calls.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_stage1(
        cities=args.cities,
        queries=args.queries,
        per_city=args.per_city,
        max_total=args.max_total,
        max_requests=args.max_requests,
        dry_run=args.dry_run,
    )
