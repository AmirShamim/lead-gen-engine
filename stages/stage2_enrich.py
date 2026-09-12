"""
Stage 2 — Live Website Crawler & Founder Extraction

Crawls target business domains using curl_cffi (Chrome TLS impersonation)
and extracts founder/owner names via a multi-tier confidence hierarchy:

  Priority 1: JSON-LD Schema.org (@type: Organization / Person) → 0.95
  Priority 2: Meta author / OpenGraph tags                       → 0.60
  Priority 3: /about, /team regex ("Founded by {Name}", "CEO")  → 0.70
  Priority 4: Footer Copyright ("© 2024 {Name}")                → 0.50

Also extracts:
  - Business summary (meta description or og:description)
  - Copyright year (for Tier 1 passive activity gate in Stage 4)
  - Domain normalization

CLI Usage:
    python -m stages.stage2_enrich
    python -m stages.stage2_enrich --batch-size 50
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cffi_requests
    HAS_CURL_CFFI = False

try:
    from config import DOMAIN_BLOCKLIST, Status
    from db import (
        get_connection,
        init_db,
        update_lead_status,
        print_funnel_summary,
    )
except ImportError:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from config import DOMAIN_BLOCKLIST, Status
    from db import (
        get_connection,
        init_db,
        update_lead_status,
        print_funnel_summary,
    )


# ============================================================
# HTML Extraction Functions
# ============================================================

def extract_business_summary(soup: BeautifulSoup) -> str | None:
    """Extract a short business description from meta tags."""
    # og:description
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        desc = og["content"].strip()
        if 20 < len(desc) < 300:
            return desc

    # meta description
    meta = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
    if meta and meta.get("content"):
        desc = meta["content"].strip()
        if 20 < len(desc) < 300:
            return desc

    return None


def extract_copyright_year(soup: BeautifulSoup) -> int | None:
    """Extract the most recent copyright year from footer/page text."""
    text = soup.get_text(separator=" ", strip=True)
    # Match patterns like "© 2024", "Copyright 2025", "©2024"
    years = re.findall(r'(?:©|copyright)\s*(\d{4})', text, re.I)
    if years:
        return max(int(y) for y in years if 2000 <= int(y) <= 2030)
    return None


def extract_general_email(soup: BeautifulSoup, domain: str) -> str | None:
    """Extract a general contact email from the page."""
    text = soup.get_text(separator=" ", strip=True)
    emails = re.findall(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        text,
    )
    # Prefer emails matching the business domain
    for email in emails:
        email_domain = email.split("@")[1].lower()
        if email_domain == domain or email_domain == f"www.{domain}":
            return email.lower()

    # Return first non-personal email
    skip_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"}
    for email in emails:
        if email.split("@")[1].lower() not in skip_domains:
            return email.lower()

    return None


def extract_founder_from_html(html: str) -> tuple[str | None, str | None, float]:
    """
    Multi-tier founder/owner extraction from HTML.
    Returns (name, title, confidence_score).
    """
    soup = BeautifulSoup(html, "lxml")

    # ── Priority 1: JSON-LD Schema.org ──────────────────────
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            raw = tag.string or ""
            data = json.loads(raw)

            # Handle @graph arrays
            items = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
            if isinstance(data, dict) and "@graph" in data:
                items = data["@graph"]

            for item in items:
                if not isinstance(item, dict):
                    continue

                # Direct founder field
                founder = item.get("founder") or item.get("foundingPerson")
                if isinstance(founder, dict) and founder.get("name"):
                    name = founder["name"].strip()
                    if 2 <= len(name.split()) <= 4:
                        return name, "Founder", 0.95
                if isinstance(founder, list) and founder:
                    for f in founder:
                        if isinstance(f, dict) and f.get("name"):
                            name = f["name"].strip()
                            if 2 <= len(name.split()) <= 4:
                                return name, "Founder", 0.95

                # Person type with jobTitle
                if item.get("@type") in ("Person", ["Person"]):
                    name = item.get("name", "").strip()
                    title = item.get("jobTitle", "").strip()
                    if name and 2 <= len(name.split()) <= 4:
                        if any(
                            role in title.lower()
                            for role in ["founder", "ceo", "owner", "president", "principal"]
                        ):
                            return name, title or "Founder", 0.95
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue

    # ── Priority 2: Meta tags ──────────────────────────────
    meta_author = soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)})
    if meta_author and meta_author.get("content"):
        author = meta_author["content"].strip()
        # Validate it looks like a real human name (2-3 words, capitalized)
        words = author.split()
        if 2 <= len(words) <= 3 and all(w[0].isupper() for w in words if w):
            return author, "Owner", 0.60

    # ── Priority 3: Body text regex ────────────────────────
    body_text = soup.get_text(separator=" ", strip=True)

    # Pattern: "Founded by FirstName LastName" or "CEO FirstName LastName"
    leadership_patterns = [
        r'(?:Founded\s+by|Founder\s*(?:&|and)\s*CEO|Co-founder)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})',
        r'(?:CEO|Chief\s+Executive\s+Officer)\s*[,:—–-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})',
        r'(?:Owner|Principal|Managing\s+Director)\s*[,:—–-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})',
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[,:—–-]?\s*(?:Founder|CEO|Owner|Principal)',
    ]

    for pattern in leadership_patterns:
        match = re.search(pattern, body_text)
        if match:
            name = match.group(1).strip()
            if 2 <= len(name.split()) <= 3:
                return name, "Founder/CEO", 0.70

    # ── Priority 4: Footer copyright name ──────────────────
    copyright_match = re.search(
        r'©\s*\d{4}\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})(?:\s|$)',
        body_text,
    )
    if copyright_match:
        name = copyright_match.group(1).strip()
        # Validate it's not just a company name (heuristic: no LLC, Inc, etc.)
        if not re.search(r'(?:LLC|Inc|Corp|Ltd|Agency|Studio|Group|Media)', name, re.I):
            if 2 <= len(name.split()) <= 3:
                return name, "Owner", 0.50

    return None, None, 0.0


# ============================================================
# Website Crawling
# ============================================================

def crawl_website(base_url: str) -> dict:
    """
    Crawl a business website across key pages.
    Returns extracted data dict.
    """
    parsed = urlparse(base_url)
    domain = parsed.netloc.replace("www.", "").lower()
    base = f"{parsed.scheme}://{parsed.netloc}"

    result = {
        "domain": domain,
        "founder_name": None,
        "founder_title": None,
        "extraction_confidence": 0.0,
        "business_summary": None,
        "general_email": None,
        "website_copyright_year": None,
        "website_status": "ACTIVE",
    }

    # Pages to crawl in priority order
    pages = [
        base_url,               # Homepage
        f"{base}/about",        # About page
        f"{base}/about-us",
        f"{base}/team",         # Team page
        f"{base}/leadership",
        f"{base}/contact",      # Contact page
    ]

    request_kwargs = {"timeout": 8}
    if HAS_CURL_CFFI:
        request_kwargs["impersonate"] = "chrome124"

    homepage_html = None

    for i, page_url in enumerate(pages):
        try:
            res = cffi_requests.get(page_url, **request_kwargs)

            # If homepage fails, site is dead
            if i == 0 and res.status_code >= 400:
                result["website_status"] = "DEAD"
                return result

            # Skip non-200 subpages (404 on /about is normal)
            if res.status_code != 200:
                continue

            html = res.text

            # Store homepage HTML for summary extraction
            if i == 0:
                homepage_html = html

            # Try founder extraction on every page
            if not result["founder_name"]:
                name, title, conf = extract_founder_from_html(html)
                if name and conf >= 0.50:
                    result["founder_name"] = name
                    result["founder_title"] = title
                    result["extraction_confidence"] = conf

            # Extract business summary from homepage
            if i == 0:
                soup = BeautifulSoup(html, "lxml")
                result["business_summary"] = extract_business_summary(soup)
                result["website_copyright_year"] = extract_copyright_year(soup)
                result["general_email"] = extract_general_email(soup, domain)

            # Early exit: if strong founder signal and summary already found, skip subpages
            if result["founder_name"] and result["extraction_confidence"] >= 0.70 and result["business_summary"]:
                break

            # Extract email from contact page
            if "contact" in page_url and not result["general_email"]:
                soup = BeautifulSoup(html, "lxml")
                result["general_email"] = extract_general_email(soup, domain)

            # Respectful delay between page crawls
            if i < len(pages) - 1:
                time.sleep(0.5)

        except Exception:
            if i == 0:
                result["website_status"] = "DEAD"
                return result
            continue

    return result


# ============================================================
# Batch Processor
# ============================================================

def run_stage2(batch_size: int = 50) -> dict:
    """
    Process DISCOVERED leads: crawl websites, extract founders.
    """
    init_db()
    conn = get_connection()

    cur = conn.execute(
        "SELECT id, website_uri, business_name, city FROM leads "
        "WHERE status = ? LIMIT ?",
        (Status.DISCOVERED, batch_size),
    )
    leads = cur.fetchall()

    if not leads:
        print("\n  ℹ️  No DISCOVERED leads to process. Stage 2 skipped.\n")
        conn.close()
        return {"processed": 0}

    print("\n" + "=" * 60)
    print("  STAGE 2: Website Crawl & Founder Extraction")
    print("=" * 60)
    print(f"  Batch size: {len(leads)} DISCOVERED leads")
    print("-" * 60)

    enriched = 0
    dead = 0
    no_leader = 0
    errors = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _crawl_worker(row):
        lead_id = row["id"]
        url = row["website_uri"]
        biz = row["business_name"]
        try:
            data = crawl_website(url)
            return lead_id, biz, data, None
        except Exception as e:
            return lead_id, biz, None, e

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(_crawl_worker, row): row for row in leads}
        for future in as_completed(future_map):
            lead_id, biz, data, exc = future.result()

            if exc:
                errors += 1
                print(f"  ❌ ERROR:   {biz} — {exc}")
                try:
                    update_lead_status(
                        conn, lead_id,
                        from_status=Status.DISCOVERED,
                        to_status=Status.DEAD_WEBSITE,
                        triggered_by="stage2",
                        reason=f"crawl_exception: {str(exc)[:200]}",
                    )
                    conn.commit()
                except Exception:
                    pass
                continue

            if data["website_status"] == "DEAD":
                update_lead_status(
                    conn, lead_id,
                    from_status=Status.DISCOVERED,
                    to_status=Status.DEAD_WEBSITE,
                    triggered_by="stage2",
                    reason="DNS/SSL failure or HTTP 4xx/5xx",
                    domain=data["domain"],
                    website_status="DEAD",
                )
                dead += 1
                print(f"  ⚫ DEAD:    {biz} ({data['domain']})")

            elif data["founder_name"] and data["extraction_confidence"] >= 0.60:
                update_lead_status(
                    conn, lead_id,
                    from_status=Status.DISCOVERED,
                    to_status=Status.ENRICHED,
                    triggered_by="stage2",
                    domain=data["domain"],
                    website_status="ACTIVE",
                    founder_name=data["founder_name"],
                    founder_title=data["founder_title"],
                    extraction_confidence=data["extraction_confidence"],
                    business_summary=data["business_summary"],
                    general_email=data["general_email"],
                    website_copyright_year=data["website_copyright_year"],
                )
                enriched += 1
                print(
                    f"  🟢 FOUND:   {data['founder_name']} ({data['founder_title']}) "
                    f"at {biz} [conf: {data['extraction_confidence']:.2f}]"
                )

            else:
                update_lead_status(
                    conn, lead_id,
                    from_status=Status.DISCOVERED,
                    to_status=Status.NO_LEADERSHIP,
                    triggered_by="stage2",
                    reason=(
                        f"confidence {data['extraction_confidence']:.2f} < 0.60"
                        if data["founder_name"]
                        else "no_founder_signal"
                    ),
                    domain=data["domain"],
                    website_status="ACTIVE",
                    business_summary=data["business_summary"],
                    website_copyright_year=data["website_copyright_year"],
                )
                no_leader += 1
                print(f"  ⚫ NO_LEAD: {biz} ({data['domain']})")

            conn.commit()

    # Summary
    print("\n" + "=" * 60)
    print("  STAGE 2 COMPLETE")
    print("=" * 60)
    print(f"  Processed:       {len(leads)}")
    print(f"  🟢 ENRICHED:     {enriched}")
    print(f"  ⚫ DEAD_WEBSITE:  {dead}")
    print(f"  ⚫ NO_LEADERSHIP: {no_leader}")
    print(f"  ❌ Errors:        {errors}")
    yield_pct = (enriched / len(leads) * 100) if leads else 0
    print(f"  Yield rate:      {yield_pct:.1f}%")
    print("=" * 60 + "\n")

    conn.close()

    return {
        "processed": len(leads),
        "enriched": enriched,
        "dead_website": dead,
        "no_leadership": no_leader,
        "errors": errors,
    }


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 2: Website Crawl & Founder Extraction")
    parser.add_argument("--batch-size", type=int, default=50, help="Leads to process per run (default: 50)")
    args = parser.parse_args()
    run_stage2(batch_size=args.batch_size)
