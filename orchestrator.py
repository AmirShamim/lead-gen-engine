"""
Lead Pipeline — Master Orchestrator

Executes pipeline stages sequentially (in series) without human intervention:
  Stage 1 (Places Ingest) → Stage 2 (Website Crawl) → Stage 3 (Bing Dorking)
  → Stage 4 (Activity Gate) → Stage 5 (AI Notes)

Features:
- Run the full pipeline or select specific stages
- Passthrough of Stage 1 parameters (cities, queries, caps)
- Real-time funnel summary after each stage
- Stage failure isolation: one stage failing doesn't crash the pipeline
- Batch-size control for Stages 2–5

CLI Usage:
    # Full pipeline with defaults (12 cities, 150/city, 2000 global cap)
    python orchestrator.py

    # Full pipeline with custom params
    python orchestrator.py --cities "Austin, TX" "Miami, FL" --per-city 100 --max-total 2000

    # Run only enrichment & personalization stages (on existing data)
    python orchestrator.py --stages 2 3 4 5

    # Run only Stage 1 with dry-run
    python orchestrator.py --stages 1 --dry-run

    # Run full pipeline with large batches
    python orchestrator.py --batch-size 100
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    DEFAULT_CITIES,
    DEFAULT_QUERIES,
    PLACES_PER_CITY_TARGET,
    PLACES_GLOBAL_LEAD_CAP,
    PLACES_MAX_REQUESTS_PER_RUN,
)
from db import get_connection, init_db, print_funnel_summary


def _import_stage(stage_num: int):
    """Lazy import a stage module. Only imports dependencies when actually needed."""
    if stage_num == 1:
        from stages.stage1_places import run_stage1
        return run_stage1
    elif stage_num == 2:
        from stages.stage2_enrich import run_stage2
        return run_stage2
    elif stage_num == 3:
        from stages.stage3_resolve import run_stage3
        return run_stage3
    elif stage_num == 4:
        from stages.stage4_activity import run_stage4
        return run_stage4
    elif stage_num == 5:
        from stages.stage5_personalize import run_stage5
        return run_stage5
    else:
        raise ValueError(f"Unknown stage: {stage_num}")


# ============================================================
# Stage Runner with Error Isolation
# ============================================================

def run_stage_safely(stage_name: str, stage_func, **kwargs) -> dict:
    """
    Run a pipeline stage with error isolation.
    If the stage crashes, log the error and return a failure result
    so subsequent stages can still execute.
    """
    print(f"\n{'━' * 60}")
    print(f"  ▶ Starting {stage_name}...")
    print(f"{'━' * 60}")

    start_time = time.time()

    try:
        result = stage_func(**kwargs)
        elapsed = time.time() - start_time
        print(f"  ⏱  {stage_name} completed in {elapsed:.1f}s")
        return result or {}

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n  ❌ {stage_name} FAILED after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


# ============================================================
# Main Orchestrator
# ============================================================

def run_pipeline(
    stages: list[int] | None = None,
    cities: list[str] | None = None,
    queries: list[str] | None = None,
    per_city: int | None = None,
    max_total: int | None = None,
    max_requests: int | None = None,
    batch_size: int = 50,
    ab_split: float = 0.0,
    dry_run: bool = False,
) -> None:
    """
    Execute the lead generation pipeline stages in series.
    """
    stages = stages or [1, 2, 3, 4, 5]
    start_time = time.time()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("\n" + "╔" + "═" * 58 + "╗")
    print("║" + "  LEAD GENERATION PIPELINE — ORCHESTRATOR".center(58) + "║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Started:  {now}".ljust(59) + "║")
    print(f"║  Stages:   {stages}".ljust(59) + "║")
    if dry_run:
        print(f"║  Mode:     DRY RUN".ljust(59) + "║")
    print("╚" + "═" * 58 + "╝")

    # Initialize database
    init_db()

    results = {}

    # ── Stage 1: Google Places Ingestion ─────────────────────
    if 1 in stages:
        run_stage1 = _import_stage(1)
        results[1] = run_stage_safely(
            "Stage 1: Google Places Ingestion",
            run_stage1,
            cities=cities,
            queries=queries,
            per_city=per_city,
            max_total=max_total,
            max_requests=max_requests,
            dry_run=dry_run,
        )

        if dry_run:
            print("\n  Dry run complete. No further stages executed.\n")
            return

    # ── Stage 2: Website Crawl & Founder Extraction ──────────
    if 2 in stages:
        run_stage2 = _import_stage(2)
        results[2] = run_stage_safely(
            "Stage 2: Website Crawl & Founder Extraction",
            run_stage2,
            batch_size=batch_size,
        )

    # ── Stage 3: LinkedIn Profile Resolution ─────────────────
    if 3 in stages:
        run_stage3 = _import_stage(3)
        results[3] = run_stage_safely(
            "Stage 3: LinkedIn Profile Resolution",
            run_stage3,
            batch_size=batch_size,
        )

    # ── Stage 4: Passive Activity Gate ───────────────────────
    if 4 in stages:
        run_stage4 = _import_stage(4)
        results[4] = run_stage_safely(
            "Stage 4: Passive Activity Gate",
            run_stage4,
            batch_size=batch_size * 2,  # Passive gate is fast; process more
        )

    # ── Stage 5: AI Personalization ──────────────────────────
    if 5 in stages:
        run_stage5 = _import_stage(5)
        results[5] = run_stage_safely(
            "Stage 5: AI Personalization",
            run_stage5,
            batch_size=batch_size,
            ab_split=ab_split,
        )

    # ── Final Summary ────────────────────────────────────────
    elapsed = time.time() - start_time
    conn = get_connection()
    print_funnel_summary(conn)
    conn.close()

    print("╔" + "═" * 58 + "╗")
    print("║" + "  PIPELINE RUN COMPLETE".center(58) + "║")
    print("╠" + "═" * 58 + "╣")
    print(f"║  Total time:  {elapsed:.1f}s ({elapsed/60:.1f} min)".ljust(59) + "║")

    for stage_num in sorted(results.keys()):
        res = results[stage_num]
        status = "❌ FAILED" if "error" in res else "✅ OK"
        print(f"║  Stage {stage_num}:     {status}".ljust(59) + "║")

    print("╚" + "═" * 58 + "╝")
    print()


# ============================================================
# CLI Entry Point
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lead Pipeline Orchestrator — Sequential Stage Execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline with defaults
  python orchestrator.py

  # Custom cities and cap
  python orchestrator.py --cities "Austin, TX" "Miami, FL" --max-total 500

  # Run only enrichment stages on existing data
  python orchestrator.py --stages 2 3 4 5

  # Dry run Stage 1 only
  python orchestrator.py --stages 1 --dry-run

  # Large batch processing
  python orchestrator.py --batch-size 100
        """,
    )

    parser.add_argument(
        "--stages",
        nargs="+",
        type=int,
        default=None,
        help="Stages to run (e.g., --stages 1 2 3). Default: all (1 2 3 4 5).",
    )
    parser.add_argument(
        "--cities",
        nargs="+",
        default=None,
        help="Target cities for Stage 1 (e.g., 'Austin, TX' 'Miami, FL').",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        help="Search queries for Stage 1 (e.g., 'growth agency' 'SEO firm').",
    )
    parser.add_argument(
        "--per-city",
        type=int,
        default=None,
        help=f"Max leads per city in Stage 1 (default: {PLACES_PER_CITY_TARGET}).",
    )
    parser.add_argument(
        "--max-total",
        type=int,
        default=None,
        help=f"Global lifetime lead cap (default: {PLACES_GLOBAL_LEAD_CAP}).",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help=f"Max API requests for Stage 1 (default: {PLACES_MAX_REQUESTS_PER_RUN}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for Stages 2–5 (default: 50).",
    )
    parser.add_argument(
        "--ab-split",
        type=float,
        default=0.0,
        help="Fraction of Stage 5 leads to get blank variant_b (default: 0.0).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview Stage 1 without making API calls.",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(
        stages=args.stages,
        cities=args.cities,
        queries=args.queries,
        per_city=args.per_city,
        max_total=args.max_total,
        max_requests=args.max_requests,
        batch_size=args.batch_size,
        ab_split=args.ab_split,
        dry_run=args.dry_run,
    )
