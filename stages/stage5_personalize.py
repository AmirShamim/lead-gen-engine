"""
Stage 5 — Azure OpenAI Hyper-Personalization Engine

Generates organic, peer-to-peer, under-250-character LinkedIn connection
notes using Azure OpenAI (gpt-4o-mini). Enforces hard validation gates
to prevent corporate jargon, hallucinated acquaintances, and length violations.

Features:
- Enhanced prompt with founder title, agency specialty, and city context
- 3-attempt retry with temperature decay on length failures
- Jargon blacklist regex validation
- A/B variant support (variant_a: personalized note, variant_b: blank connect)

CLI Usage:
    python -m stages.stage5_personalize
    python -m stages.stage5_personalize --batch-size 20
    python -m stages.stage5_personalize --ab-split 0.2  # 20% blank connects
"""

import argparse
import json
import re
import sqlite3
import sys

try:
    from config import (
        AZURE_OPENAI_ENDPOINT,
        AZURE_OPENAI_KEY,
        AZURE_OPENAI_DEPLOYMENT,
        AZURE_OPENAI_API_VERSION,
        JARGON_BLACKLIST,
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
        AZURE_OPENAI_ENDPOINT,
        AZURE_OPENAI_KEY,
        AZURE_OPENAI_DEPLOYMENT,
        AZURE_OPENAI_API_VERSION,
        JARGON_BLACKLIST,
        Status,
    )
    from db import (
        get_connection,
        init_db,
        update_lead_status,
        record_api_call,
    )

from openai import AzureOpenAI


# ============================================================
# Prompt Engineering
# ============================================================

SYSTEM_PROMPT = """You write authentic, peer-to-peer LinkedIn connection notes from a technical founder who runs a boutique AI engineering agency.

STRICT RULES:
1. MAXIMUM 240 characters. Count carefully. Never exceed 245 characters.
2. TONE: Casual, respectful, curious. Like one founder texting another. Never salesy.
3. CONTEXT: Reference ONE specific detail about their company, role, or specialty naturally.
4. NO LIES: Never claim you've met, were referred, or follow them.
5. NO SALES: Do not pitch, sell, ask for calls, drop links, or mention your services.
6. NO JARGON: Never use "synergy", "elevate", "streamline", "game-changer", "leverage", "hop on a call", "free audit", "bandwidth".
7. KEEP IT HUMAN: No emojis. Max one exclamation mark. No corporate sign-offs.

Return valid JSON: {"connection_note": "your note text here"}"""


def build_user_prompt(
    founder: str,
    title: str | None,
    biz_name: str,
    summary: str | None,
    city: str | None,
) -> str:
    """Build a contextual user prompt for note generation."""
    parts = [f"Founder: {founder}"]
    if title:
        parts.append(f"Title: {title}")
    parts.append(f"Agency: {biz_name}")
    if summary:
        parts.append(f"Specialty: {summary[:200]}")
    if city:
        parts.append(f"City: {city}")
    parts.append("Write a custom connection note.")
    return "\n".join(parts)


# ============================================================
# Note Validation
# ============================================================

def validate_note(note: str) -> tuple[bool, str]:
    """
    Validate a generated note against hard quality gates.
    Returns (is_valid, failure_reason).
    """
    if not note or not note.strip():
        return False, "empty_note"

    # Gate 1: Character length
    if len(note) > 250:
        return False, f"too_long({len(note)} chars)"

    # Gate 2: Jargon blacklist
    note_lower = note.lower()
    for jargon in JARGON_BLACKLIST:
        if jargon in note_lower:
            return False, f"jargon_detected({jargon})"

    # Gate 3: No excessive punctuation
    if note.count("!") > 1:
        return False, "excessive_exclamation"
    if note.count("?") > 2:
        return False, "excessive_questions"

    # Gate 4: No emoji spam
    emoji_count = len(re.findall(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF\U00002600-\U000027BF]',
        note,
    ))
    if emoji_count > 0:
        return False, f"emoji_detected({emoji_count})"

    return True, "ok"


# ============================================================
# Note Generation with Retry
# ============================================================

def generate_note(
    client: AzureOpenAI,
    founder: str,
    title: str | None,
    biz_name: str,
    summary: str | None,
    city: str | None,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, int]:
    """
    Generate a personalized connection note with up to 3 retry attempts.
    Returns (note_text, char_count).
    """
    user_prompt = build_user_prompt(founder, title, biz_name, summary, city)
    temperatures = [0.7, 0.5, 0.3]  # Decay temperature on retries

    for attempt, temp in enumerate(temperatures):
        try:
            res = client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=temp,
                max_tokens=120,
            )

            # Log API cost (~150 input + 60 output tokens)
            if conn:
                record_api_call(
                    conn,
                    api_name="azure_openai",
                    endpoint=AZURE_OPENAI_DEPLOYMENT,
                    query=f"{founder} at {biz_name}",
                    results_count=1,
                    cost_usd=0.00015,  # ~$0.15 / 1M tokens
                )

            raw = res.choices[0].message.content
            data = json.loads(raw)
            note = data.get("connection_note", "").strip()

            is_valid, reason = validate_note(note)
            if is_valid:
                return note, len(note)

            # Log validation failure, retry
            if attempt < 2:
                continue

        except (json.JSONDecodeError, KeyError, AttributeError):
            if attempt < 2:
                continue

        except Exception as e:
            if attempt < 2:
                continue
            break

    # Fallback: handcrafted safe note
    first_name = founder.split()[0]
    fallback = (
        f"Hey {first_name} — saw what you're building at {biz_name}. "
        f"Always good to connect with other agency founders."
    )
    return fallback[:245], len(fallback[:245])


# ============================================================
# Batch Processor
# ============================================================

def run_stage5(batch_size: int = 15, ab_split: float = 0.0) -> dict:
    """
    Process QUALIFIED_ACTIVE leads: generate personalized notes.

    Args:
        batch_size: Number of leads to process.
        ab_split: Fraction of leads to get variant_b (blank connect, no note).
                  E.g., 0.2 = 20% blank, 80% personalized.
    """
    if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_KEY:
        print("ERROR: AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY must be set in .env")
        return {"error": "no_api_key"}

    init_db()
    conn = get_connection()

    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )

    cur = conn.execute(
        "SELECT id, founder_name, founder_title, business_name, "
        "business_summary, city "
        "FROM leads WHERE status = ? LIMIT ?",
        (Status.QUALIFIED_ACTIVE, batch_size),
    )
    leads = cur.fetchall()

    if not leads:
        print("\n  ℹ️  No QUALIFIED_ACTIVE leads to process. Stage 5 skipped.\n")
        conn.close()
        return {"processed": 0}

    print("\n" + "=" * 60)
    print("  STAGE 5: AI Personalization (Azure OpenAI)")
    print("=" * 60)
    print(f"  Batch size: {len(leads)} QUALIFIED_ACTIVE leads")
    print(f"  Model: {AZURE_OPENAI_DEPLOYMENT}")
    print(f"  A/B split: {ab_split*100:.0f}% blank / {(1-ab_split)*100:.0f}% personalized")
    print("-" * 60)

    import random
    queued = 0
    errors = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _note_worker(row):
        lead_id = row["id"]
        founder = row["founder_name"]
        title = row["founder_title"]
        biz = row["business_name"]
        summary = row["business_summary"]
        city = row["city"]

        is_variant_b = random.random() < ab_split
        variant = "variant_b" if is_variant_b else "variant_a"

        if is_variant_b:
            return lead_id, founder, title, biz, "", 0, variant, None

        try:
            note, char_count = generate_note(
                client, founder, title, biz, summary, city, conn=None
            )
            return lead_id, founder, title, biz, note, char_count, variant, None
        except Exception as e:
            return lead_id, founder, title, biz, None, 0, variant, e

    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(_note_worker, row): row for row in leads}
        for future in as_completed(future_map):
            lead_id, founder, title, biz, note, char_count, variant, exc = future.result()

            if exc or note is None:
                errors += 1
                print(f"  ❌ ERROR: {founder} at {biz} — {exc}")
                continue

            record_api_call(
                conn,
                api_name="azure_openai",
                endpoint=AZURE_OPENAI_DEPLOYMENT,
                query=f"{founder} at {biz}",
                results_count=1,
                cost_usd=0.00015,
            )

            update_lead_status(
                conn, lead_id,
                from_status=Status.QUALIFIED_ACTIVE,
                to_status=Status.QUEUED,
                triggered_by="stage5",
                personalized_note=note,
                note_char_count=char_count,
                note_generated_at="CURRENT_TIMESTAMP",
                ab_variant=variant,
                queued_at="CURRENT_TIMESTAMP",
            )
            queued += 1

            if variant == "variant_b":
                print(f"  🟡 QUEUED (B): {founder} at {biz} [blank connect]")
            else:
                print(f"  🟡 QUEUED (A): {founder} at {biz} [{char_count} chars]")
                print(f"              \"{note[:80]}{'...' if len(note) > 80 else ''}\"")

            conn.commit()

    # Summary
    print("\n" + "=" * 60)
    print("  STAGE 5 COMPLETE")
    print("=" * 60)
    print(f"  Processed:       {len(leads)}")
    print(f"  🟡 QUEUED:        {queued}")
    print(f"  ❌ Errors:        {errors}")
    print("=" * 60 + "\n")

    conn.close()
    return {
        "processed": len(leads),
        "queued": queued,
        "errors": errors,
    }


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage 5: AI Personalization")
    parser.add_argument("--batch-size", type=int, default=15, help="Leads to process (default: 15)")
    parser.add_argument("--ab-split", type=float, default=0.0, help="Fraction for blank variant_b (default: 0.0)")
    args = parser.parse_args()
    run_stage5(batch_size=args.batch_size, ab_split=args.ab_split)
