"""
Stage 3 — Search Engine LinkedIn Profile Resolution (Search-First Dual-Pass Engine)

Resolves candidate personal LinkedIn profile URLs via search dorking
with automatic decision-maker ranking:
- Phase 1: Named Founder Resolution (cleans noise like "Leadership", "Meet", "Bio")
- Phase 2: Company Decision-Maker Dorking (site:linkedin.com/in/ (founder OR owner OR ceo) "{company}")
- Multi-decision maker ranking: selects ONLY the single most active, authoritative leader
- Rescues leads where website crawler found NO_LEADERSHIP or dirty names

CLI Usage:
    python -m stages.stage3_resolve
    python -m stages.stage3_resolve --batch-size 50
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from urllib.parse import urlparse

import requests

try:
    from config import (
        SEARCH_PROVIDER,
        SERPER_API_KEY,
        SERPER_ENDPOINT,
        GOOGLE_SEARCH_API_KEY,
        GOOGLE_SEARCH_ENGINE_ID,
        GOOGLE_SEARCH_ENDPOINT,
        AZURE_BING_SEARCH_KEY,
        BING_ENDPOINT,
        SEARCH_COST_PER_REQUEST,
        NAME_VARIANTS,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        update_lead_status,
        record_api_call,
    )
except ImportError:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from config import (
        SEARCH_PROVIDER,
        SERPER_API_KEY,
        SERPER_ENDPOINT,
        GOOGLE_SEARCH_API_KEY,
        GOOGLE_SEARCH_ENGINE_ID,
        GOOGLE_SEARCH_ENDPOINT,
        AZURE_BING_SEARCH_KEY,
        BING_ENDPOINT,
        SEARCH_COST_PER_REQUEST,
        NAME_VARIANTS,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        update_lead_status,
        record_api_call,
    )


# Industries that indicate a wrong-person match
WRONG_INDUSTRY_SIGNALS = frozenset({
    "nurse", "nursing", "hospital", "medical", "doctor", "physician",
    "teacher", "professor", "school district", "university",
    "police", "fire department", "military",
    "real estate agent", "realtor",
    "attorney", "law firm", "paralegal",
    "accountant", "accounting firm",
})

NON_HUMAN_WORDS = frozenset({
    "tactics", "asset", "reasons", "business", "operations", "marketing",
    "seo", "agency", "digital", "network", "solutions", "team", "growth",
    "creative", "media", "internet", "company", "group", "consulting",
    "services", "technologies", "studios", "partners", "management",
    "equipment", "supply", "supplies", "hardware", "consolidated", "holdings",
    "enterprises", "properties", "capital", "ventures", "corporation"
})

PREFIX_NOISE = re.compile(
    r'^(?:Leadership|Leader|Founder|Co-founder|Owner|CEO|President|Principal|Managing\s+Director|Meet|Read\s+Bio|About|Dr\.?|Mr\.?|Ms\.?)\s+',
    re.I
)
SUFFIX_NOISE = re.compile(
    r'\s+(?:Founder|CEO|Owner|Co|LLC|Inc|Agency|Group)$',
    re.I
)


# ============================================================
# Name & Company Sanitizers
# ============================================================

def clean_company_name(biz_name: str) -> str:
    """
    Clean business name from Google Places to maximize LinkedIn search match:
    - Strips prefixes: "Austin SEO - Neon Ambition" -> "Neon Ambition"
    - Strips suffixes: "LLC", "Inc", "Corp", "Ltd"
    - Strips pipes: "Motiliti | Austin SEO" -> "Motiliti"
    - Strips parentheticals: "Rodeo Creative (formerly Parrot Digital)" -> "Rodeo Creative"
    """
    if not biz_name:
        return ""
    cleaned = biz_name.strip()
    cleaned = re.sub(r'\s*\(.*?\)', '', cleaned)
    
    if '|' in cleaned:
        parts = cleaned.split('|')
        cleaned = parts[0].strip() if len(parts[0].strip()) <= len(parts[1].strip()) else parts[1].strip()
    elif ' - ' in cleaned:
        parts = cleaned.split(' - ')
        # If first part is a generic service/city, pick second part
        if any(term in parts[0].lower() for term in ['seo', 'marketing', 'agency', 'austin', 'miami', 'web design']):
            cleaned = parts[1].strip()
        else:
            cleaned = parts[0].strip()

    cleaned = re.sub(r'\b(?:LLC|Inc\.?|Corp\.?|Ltd\.?|Co\.?)\b', '', cleaned, flags=re.I).strip()
    cleaned = cleaned.rstrip(' ,.-')
    return cleaned if len(cleaned) >= 2 else biz_name


def clean_founder_name(name: str | None) -> str | None:
    """
    Clean extracted founder name by stripping noise prefixes (Meet, Leadership, Read Bio)
    and rejecting non-human strings (Tactics Full Asset, Reasons Why Business).
    """
    if not name:
        return None
    cleaned = name.strip()
    cleaned = PREFIX_NOISE.sub('', cleaned).strip()
    cleaned = SUFFIX_NOISE.sub('', cleaned).strip()
    
    words = cleaned.split()
    if len(words) < 2 or len(words) > 4:
        return None
    
    # Reject if any word is a non-human keyword
    if any(w.lower() in NON_HUMAN_WORDS for w in words):
        return None
        
    return cleaned


# ============================================================
# Decision Maker Scoring & Selection (Only Most Active)
# ============================================================

def extract_decision_maker_from_result(
    item: dict,
    clean_biz: str,
    city: str | None = None,
) -> dict | None:
    """
    Parse a search result item to extract and score a candidate decision maker.
    Returns candidate dict or None.
    """
    title_text = item.get("title", "")
    snippet_text = item.get("snippet", "")
    url = item.get("url", "") or item.get("link", "")
    
    if "linkedin.com/in/" not in url:
        return None
        
    full_text = f"{title_text} {snippet_text}".lower()
    
    # Company match check: clean_biz or core word must appear in result
    biz_words = [w for w in clean_biz.lower().split() if len(w) > 2 and w not in NON_HUMAN_WORDS]
    if not any(bw in full_text for bw in biz_words):
        return None
        
    # Extract name and title from LinkedIn result title
    # Format typically: "Name - Title at Company | LinkedIn"
    cleaned_title = re.sub(r'\s*[-|]\s*LinkedIn.*$', '', title_text, flags=re.I).strip()
    parts = re.split(r'\s+[-–—|]\s+', cleaned_title, maxsplit=1)
    if len(parts) < 2:
        return None
        
    raw_name, raw_role = parts[0].strip(), parts[1].strip()
    name = clean_founder_name(raw_name)
    if not name:
        return None
        
    # Title hierarchy scoring (Authority level)
    role_lower = raw_role.lower()
    role_score = 0
    clean_role = "Founder/CEO"
    
    if any(t in role_lower for t in ["founder", "co-founder", "founding"]):
        role_score = 40
        clean_role = "Founder"
    elif any(t in role_lower for t in ["owner", "co-owner"]):
        role_score = 38
        clean_role = "Owner"
    elif any(t in role_lower for t in ["ceo", "chief executive"]):
        role_score = 35
        clean_role = "CEO"
    elif "president" in role_lower:
        role_score = 30
        clean_role = "President"
    elif any(t in role_lower for t in ["managing director", "principal", "partner"]):
        role_score = 25
        clean_role = "Principal"
    else:
        # Fallback check in snippet
        if any(t in full_text for t in ["founder", "owner", "ceo", "president"]):
            role_score = 20
            clean_role = "Founder/CEO"
        else:
            return None  # Not a decision maker!
            
    # Activity & currency signals from snippet
    activity_score = 0
    if "present" in full_text or "current" in full_text:
        activity_score += 25  # Currently in the role
    if "500+ connections" in full_text:
        activity_score += 15
    elif "connections" in full_text:
        activity_score += 5
        
    # Dormant / negative signals
    if any(sig in full_text for sig in ["former", "previously", "retired", "ex-"]):
        activity_score -= 50
    if any(sig in full_text for sig in WRONG_INDUSTRY_SIGNALS):
        activity_score -= 40
        
    # Geographic confirmation
    geo_score = 0
    if city:
        city_clean = city.split(",")[0].strip().lower()
        if city_clean in full_text:
            geo_score += 15
            
    total_score = role_score + activity_score + geo_score
    if total_score < 35:
        return None
        
    slug_m = re.search(r'linkedin\.com/in/([^/?]+)', url)
    slug = slug_m.group(1) if slug_m else None
    
    headline = raw_role
    profile_details = {
        "headline": headline,
        "clean_name": name,
        "clean_title": clean_role,
        "authority_level": clean_role,
        "connections": (
            "500+" if "500+" in full_text
            else (re.search(r'(\d+)\+?\s*connections?', full_text).group(0) if re.search(r'(\d+)\+?\s*connections?', full_text) else None)
        ),
        "has_present_role": "present" in full_text or "current" in full_text,
        "location": city or None,
        "score": total_score,
        "snippet_preview": snippet_text[:200],
    }

    return {
        "name": name,
        "title": clean_role,
        "headline": headline,
        "profile_details": profile_details,
        "url": url,
        "slug": slug,
        "score": total_score,
        "snippet": f"{title_text} | {snippet_text}",
    }


# ============================================================
# Search Engine API Execution
# ============================================================

def execute_search(query: str, conn: sqlite3.Connection | None = None) -> list[dict]:
    """
    Execute search query using Serper, Google Custom Search, or Azure Bing.
    Returns list of items: [{'url': ..., 'title': ..., 'snippet': ...}, ...]
    """
    # 1. Serper.dev (Google Search API — Recommended)
    if SEARCH_PROVIDER == "serper" or (SERPER_API_KEY and not GOOGLE_SEARCH_ENGINE_ID and not AZURE_BING_SEARCH_KEY):
        if not SERPER_API_KEY:
            raise ValueError("SERPER_API_KEY not set in .env")
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        }
        res = requests.post(
            SERPER_ENDPOINT,
            headers=headers,
            json={"q": query, "num": 5},
            timeout=10,
        )
        res.raise_for_status()

        if conn:
            record_api_call(
                conn,
                api_name="serper_google",
                endpoint="search",
                query=query,
                results_count=0,
                cost_usd=0.0,
            )

        data = res.json()
        items = data.get("organic", [])
        return [
            {
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items
        ]

    # 2. Google Custom Search JSON API
    elif SEARCH_PROVIDER == "google":
        if not GOOGLE_SEARCH_ENGINE_ID or not GOOGLE_SEARCH_API_KEY:
            raise ValueError("Google Search credentials missing in .env")
        params = {
            "key": GOOGLE_SEARCH_API_KEY,
            "cx": GOOGLE_SEARCH_ENGINE_ID,
            "q": query,
            "num": 5,
        }
        res = requests.get(GOOGLE_SEARCH_ENDPOINT, params=params, timeout=10)
        res.raise_for_status()

        record_api_call(
            conn,
            api_name="google_custom_search",
            endpoint="customsearch/v1",
            query=query,
            results_count=0,
            cost_usd=SEARCH_COST_PER_REQUEST,
        )

        data = res.json()
        items = data.get("items", [])
        return [
            {
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            }
            for item in items
        ]

    # 3. Azure Bing Search API
    else:
        headers = {"Ocp-Apim-Subscription-Key": AZURE_BING_SEARCH_KEY}
        params = {"q": query, "count": 5, "mkt": "en-US"}
        res = requests.get(BING_ENDPOINT, headers=headers, params=params, timeout=10)
        res.raise_for_status()

        record_api_call(
            conn,
            api_name="bing_search",
            endpoint="v7.0/search",
            query=query,
            results_count=0,
            cost_usd=SEARCH_COST_PER_REQUEST,
        )

        data = res.json()
        web_pages = data.get("webPages", {}).get("value", [])
        return [
            {
                "url": page.get("url", ""),
                "title": page.get("name", ""),
                "snippet": page.get("snippet", ""),
            }
            for page in web_pages
        ]


# ============================================================
# Dual-Pass Single Lead Resolution
# ============================================================

def resolve_single_lead(
    founder_name: str | None,
    biz_name: str,
    city: str | None,
    conn: sqlite3.Connection | None = None,
) -> tuple[str | None, str | None, str | None, float, str | None, str | None, str | None, int]:
    """
    Attempt to resolve a LinkedIn profile for a founder or company.
    
    Returns:
        (url, slug, dork_query, confidence, snippet, resolved_name, resolved_title, other_cands_count)
    """
    clean_biz = clean_company_name(biz_name)
    cleaned_founder = clean_founder_name(founder_name)
    
    # ── Phase 1: Named Search (if clean founder name is available) ──
    if cleaned_founder:
        query = f'site:linkedin.com/in/ "{cleaned_founder}" "{clean_biz}"'
        try:
            pages = execute_search(query, conn)
            for page in pages:
                url = page.get("url", "")
                if "linkedin.com/in/" not in url:
                    continue
                cand = extract_decision_maker_from_result(page, clean_biz, city)
                if cand and cand["score"] >= 50:
                    confidence = min(cand["score"] / 100.0, 1.0)
                    return (
                        cand["url"], cand["slug"], query, confidence,
                        cand["snippet"], cand["name"], cand["title"], 0,
                        cand.get("headline"), cand.get("profile_details")
                    )
            time.sleep(0.2)
        except Exception:
            pass

    # ── Phase 2: Company-Level Decision Maker Dorking (Fallback or Direct) ──
    # Targets all decision makers at the company and picks ONLY the most active one
    query = f'site:linkedin.com/in/ (founder OR owner OR ceo OR president) "{clean_biz}"'
    try:
        pages = execute_search(query, conn)
        candidates = []
        for page in pages:
            cand = extract_decision_maker_from_result(page, clean_biz, city)
            if cand:
                candidates.append(cand)

        if candidates:
            # Sort by score descending: picks single most active/authoritative decision maker
            candidates.sort(key=lambda c: c["score"], reverse=True)
            best = candidates[0]
            confidence = min(best["score"] / 100.0, 1.0)
            other_count = len(candidates) - 1
            return (
                best["url"], best["slug"], query, confidence,
                best["snippet"], best["name"], best["title"], other_count,
                best.get("headline"), best.get("profile_details")
            )
    except Exception:
        pass

    return None, None, query, 0.0, None, cleaned_founder, None, 0, None, None


# ============================================================
# Batch Processor
# ============================================================

def run_stage3(batch_size: int = 50, rescue_all: bool = True) -> dict:
    """
    Process leads: resolve LinkedIn profiles via search dorking.
    Picks ONLY the single most active decision maker per company.
    Supports rescuing leads previously marked as NO_LEADERSHIP or NO_LINKEDIN.
    """
    if SEARCH_PROVIDER == "serper" or (SERPER_API_KEY and not GOOGLE_SEARCH_ENGINE_ID and not AZURE_BING_SEARCH_KEY):
        if not SERPER_API_KEY:
            print("ERROR: SERPER_API_KEY not set in .env file.")
            return {"error": "no_serper_key"}
        provider_name = "Serper.dev Google Search (Decision-Maker Engine)"
    elif SEARCH_PROVIDER == "google":
        if not GOOGLE_SEARCH_ENGINE_ID or not GOOGLE_SEARCH_API_KEY:
            print("ERROR: Google Search credentials not set in .env.")
            return {"error": "no_api_key"}
        provider_name = "Google Custom Search JSON API"
    else:
        if not AZURE_BING_SEARCH_KEY:
            print("ERROR: AZURE_BING_SEARCH_KEY not set in .env file.")
            return {"error": "no_api_key"}
        provider_name = "Azure Bing Web Search API"

    init_db()
    conn = get_connection()

    # Query leads to process: Enriched, plus rescue any NO_LEADERSHIP or NO_LINKEDIN
    if rescue_all:
        cur = conn.execute(
            "SELECT id, founder_name, founder_title, business_name, city, domain, status "
            "FROM leads WHERE status IN (?, ?, ?) "
            "ORDER BY CASE status "
            "  WHEN ? THEN 1 "
            "  WHEN ? THEN 2 "
            "  ELSE 3 END "
            "LIMIT ?",
            (Status.ENRICHED, Status.NO_LEADERSHIP, Status.NO_LINKEDIN,
             Status.ENRICHED, Status.NO_LEADERSHIP, batch_size),
        )
    else:
        cur = conn.execute(
            "SELECT id, founder_name, founder_title, business_name, city, domain, status "
            "FROM leads WHERE status = ? LIMIT ?",
            (Status.ENRICHED, batch_size),
        )
    leads = cur.fetchall()

    if not leads:
        print("\n  ℹ️  No candidate leads to process in Stage 3.\n")
        conn.close()
        return {"processed": 0}

    print("\n" + "=" * 60)
    print(f"  STAGE 3: Decision-Maker Resolution ({provider_name})")
    print("=" * 60)
    print(f"  Batch size: {len(leads)} leads")
    print("  Rule: Resolves SINGLE most active decision maker per company")
    print("-" * 60)

    resolved = 0
    no_match = 0
    errors = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _resolve_worker(row):
        lead_id = row["id"]
        founder = row["founder_name"]
        biz = row["business_name"]
        city = row["city"]
        prev_status = row["status"]
        try:
            url, slug, query, confidence, snippet, res_name, res_title, other_count, res_headline, res_details = resolve_single_lead(
                founder, biz, city, conn=None
            )
            return lead_id, founder, biz, city, prev_status, url, slug, query, confidence, snippet, res_name, res_title, other_count, res_headline, res_details, None
        except Exception as e:
            return lead_id, founder, biz, city, prev_status, None, None, None, 0.0, None, None, None, 0, None, None, e

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {executor.submit(_resolve_worker, row): row for row in leads}
        for future in as_completed(future_map):
            lead_id, founder, biz, city, prev_status, url, slug, query, confidence, snippet, res_name, res_title, other_count, res_headline, res_details, exc = future.result()

            if exc:
                errors += 1
                print(f"  ❌ ERROR: {biz} — {exc}")
                continue

            # Record API call in DB
            record_api_call(
                conn,
                api_name="serper_google",
                endpoint="search",
                query=query or biz,
                results_count=1 if url else 0,
                cost_usd=0.0,
            )

            if url and confidence >= 0.35:
                update_lead_status(
                    conn, lead_id,
                    from_status=prev_status,
                    to_status=Status.LINKEDIN_RESOLVED,
                    triggered_by="stage3",
                    founder_name=res_name,
                    founder_title=res_title,
                    linkedin_url=url,
                    linkedin_slug=slug,
                    linkedin_headline=res_headline,
                    founder_profile_details=json.dumps(res_details) if res_details else None,
                    search_dork_query=query,
                    search_dork_snippet=snippet,
                    resolution_confidence=confidence,
                )
                resolved += 1
                multi_note = f" (Selected top from {other_count + 1} decision makers)" if other_count > 0 else ""
                headline_preview = f" | {res_headline[:40]}..." if res_headline else ""
                print(f"  🟢 RESOLVED: {res_name} ({res_title}) at {biz} → {slug} [conf: {confidence:.2f}]{multi_note}{headline_preview}")
            else:
                if prev_status != Status.NO_LINKEDIN:
                    update_lead_status(
                        conn, lead_id,
                        from_status=prev_status,
                        to_status=Status.NO_LINKEDIN,
                        triggered_by="stage3",
                        reason="No decision maker matched on LinkedIn",
                        search_dork_query=query,
                    )
                no_match += 1
                print(f"  ⚫ NO_MATCH: {biz} ({clean_company_name(biz)})")

            conn.commit()

    # Summary
    print("\n" + "=" * 60)
    print("  STAGE 3 COMPLETE")
    print("=" * 60)
    print(f"  Processed:           {len(leads)}")
    print(f"  🟢 LINKEDIN_RESOLVED: {resolved}")
    print(f"  ⚫ NO_LINKEDIN:       {no_match}")
    print(f"  ❌ Errors:            {errors}")
    yield_pct = (resolved / len(leads) * 100) if leads else 0
    print(f"  Resolution rate:     {yield_pct:.1f}%")
    print("=" * 60 + "\n")

    conn.close()
    return {
        "processed": len(leads),
        "resolved": resolved,
        "no_linkedin": no_match,
        "errors": errors,
    }


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 3: Decision-Maker Resolution")
    parser.add_argument("--batch-size", type=int, default=50, help="Leads to process per run (default: 50)")
    args = parser.parse_args()
    run_stage3(batch_size=args.batch_size)
