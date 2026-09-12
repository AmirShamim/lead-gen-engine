"""
Pipeline Runner — UI-Triggered Async Execution Controller

Enables portal users to run pipeline stages directly from the web interface:
- No CLI required
- Strict batch limit enforcement (never exceeds user-selected batch size)
- Strict AI opening text toggle (only calls Azure OpenAI when explicitly toggled ON)
- Real-time job logs and progress tracking for the portal UI
"""

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Any

from config import (
    Status,
    FOLLOWER_SWEET_SPOT_MIN,
    FOLLOWER_SWEET_SPOT_MAX,
    FOLLOWER_EXCLUDE_LIMIT,
)
from db import get_connection, init_db, update_lead_status, backfill_follower_tiers


# ============================================================
# In-Memory Job Registry
# ============================================================

class JobRegistry:
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create_job(self, stage_name: str, batch_size: int, generate_ai_note: bool) -> str:
        job_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "stage": stage_name,
                "status": "QUEUED",
                "progress": 0,
                "total": batch_size,
                "generate_ai_note": generate_ai_note,
                "logs": [f"[{self._now()}] Job created. Initializing {stage_name} (Batch: {batch_size}, AI Note: {generate_ai_note})..."],
                "created_at": self._now(),
                "started_at": None,
                "finished_at": None,
                "error": None,
                "result": None,
            }
        return job_id

    def log(self, job_id: str, message: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["logs"].append(f"[{self._now()}] {message}")

    def update_progress(self, job_id: str, progress: int, total: int | None = None) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["progress"] = progress
                if total is not None:
                    self._jobs[job_id]["total"] = total

    def start(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "RUNNING"
                self._jobs[job_id]["started_at"] = self._now()

    def complete(self, job_id: str, result: dict | None = None) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "COMPLETED"
                self._jobs[job_id]["finished_at"] = self._now()
                self._jobs[job_id]["result"] = result or {}

    def fail(self, job_id: str, error_msg: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "FAILED"
                self._jobs[job_id]["finished_at"] = self._now()
                self._jobs[job_id]["error"] = error_msg
                self._jobs[job_id]["logs"].append(f"[{self._now()}] ❌ ERROR: {error_msg}")

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def get_recent_jobs(self, limit: int = 10) -> list[dict]:
        with self._lock:
            sorted_jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
            return [dict(j) for j in sorted_jobs[:limit]]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")


registry = JobRegistry()


# ============================================================
# Stage Execution Logic (Strict Batch & AI Toggle Enforcement)
# ============================================================

def _execute_stage_worker(
    job_id: str,
    stage_id: str,
    batch_size: int,
    generate_ai_note: bool,
    follower_filter: str = "ALL",
    cities: list[str] | None = None,
    queries: list[str] | None = None,
) -> None:
    """Worker function executed in background thread."""
    registry.start(job_id)
    init_db()

    try:
        if stage_id == "stage1":
            # Stage 1: Google Places Ingestion
            from stages.stage1_places import run_stage1
            registry.log(job_id, f"Launching Stage 1 Places Ingest (max_total={batch_size})...")
            res = run_stage1(
                cities=cities,
                queries=queries,
                per_city=min(batch_size, 25),
                max_total=batch_size,
            )
            registry.log(job_id, f"Stage 1 finished: {res}")
            registry.complete(job_id, res)

        elif stage_id == "stage2":
            # Stage 2: Website Crawl & Enrichment
            from stages.stage2_enrich import run_stage2
            registry.log(job_id, f"Launching Stage 2 Website Crawl (Batch limit: {batch_size})...")
            res = run_stage2(batch_size=batch_size)
            registry.log(job_id, f"Stage 2 finished: {res}")
            registry.complete(job_id, res)

        elif stage_id == "stage3":
            # Stage 3: LinkedIn Resolution
            from stages.stage3_resolve import run_stage3
            registry.log(job_id, f"Launching Stage 3 LinkedIn Search (Batch limit: {batch_size})...")
            res = run_stage3(batch_size=batch_size)
            # Update follower tiers
            conn = get_connection()
            backfill_follower_tiers(conn)
            conn.close()
            registry.log(job_id, f"Stage 3 finished: {res}")
            registry.complete(job_id, res)

        elif stage_id == "stage4":
            # Stage 4: Activity Gate
            from stages.stage4_activity import run_stage4
            registry.log(job_id, f"Launching Stage 4 Deterministic Gate (Batch limit: {batch_size})...")
            res = run_stage4(batch_size=batch_size)
            registry.log(job_id, f"Stage 4 finished: {res}")
            registry.complete(job_id, res)

        elif stage_id == "stage5":
            # Stage 5: AI Personalization OR Blank Queuing
            res = _run_controlled_stage5(job_id, batch_size, generate_ai_note, follower_filter)
            registry.complete(job_id, res)

        elif stage_id == "pipeline_enrich":
            # Pipeline: Stages 2 -> 3 -> 4 (and optional 5)
            registry.log(job_id, f"Running Pipeline (Stages 2 -> 4) for batch size {batch_size}...")
            from stages.stage2_enrich import run_stage2
            from stages.stage3_resolve import run_stage3
            from stages.stage4_activity import run_stage4

            registry.log(job_id, "▶ Stage 2: Website Crawl...")
            r2 = run_stage2(batch_size=batch_size)
            registry.log(job_id, f"Stage 2 result: {r2}")

            registry.log(job_id, "▶ Stage 3: LinkedIn Resolution...")
            r3 = run_stage3(batch_size=batch_size)
            conn = get_connection()
            backfill_follower_tiers(conn)
            conn.close()
            registry.log(job_id, f"Stage 3 result: {r3}")

            registry.log(job_id, "▶ Stage 4: Activity Gate...")
            r4 = run_stage4(batch_size=batch_size)
            registry.log(job_id, f"Stage 4 result: {r4}")

            r5 = None
            if generate_ai_note:
                registry.log(job_id, "▶ Stage 5: AI Personalization (Toggled ON)...")
                r5 = _run_controlled_stage5(job_id, batch_size, True, follower_filter)
            else:
                registry.log(job_id, "▶ Stage 5 Skipped (AI Note toggle is OFF: leads ready for Blank Connect).")

            res = {"stage2": r2, "stage3": r3, "stage4": r4, "stage5": r5}
            registry.complete(job_id, res)

        else:
            raise ValueError(f"Unknown stage: {stage_id}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        registry.fail(job_id, str(e))


def _run_controlled_stage5(
    job_id: str,
    batch_size: int,
    generate_ai_note: bool,
    follower_filter: str,
) -> dict:
    """
    Executes Stage 5 with strict batch limits and strict AI toggle enforcement.
    If generate_ai_note is False: moves leads to QUEUED with variant_b (Blank) at $0 cost!
    If generate_ai_note is True: calls Azure OpenAI strictly up to batch_size.
    """
    conn = get_connection()

    # Build query with optional follower filter
    where_parts = ["status = 'QUALIFIED_ACTIVE'"]
    params = []

    if follower_filter == "SWEET_SPOT_500_5K":
        where_parts.append("follower_tier = 'SWEET_SPOT_500_5K'")
    elif follower_filter == "EXCLUDE_OVER_5K":
        where_parts.append("follower_tier != 'OVER_5K'")

    where_sql = " AND ".join(where_parts)
    query = f"""
        SELECT id, founder_name, founder_title, business_name, business_summary, city, follower_tier
        FROM leads
        WHERE {where_sql}
        ORDER BY created_at ASC
        LIMIT ?
    """
    params.append(batch_size)

    rows = conn.execute(query, params).fetchall()
    leads = [dict(r) for r in rows]

    if not leads:
        registry.log(job_id, "ℹ️ No QUALIFIED_ACTIVE leads matching filter. Nothing to queue.")
        conn.close()
        return {"queued": 0, "message": "No qualified leads available"}

    registry.log(job_id, f"Found {len(leads)} QUALIFIED_ACTIVE leads (Strict batch cap: {batch_size}).")

    queued_count = 0

    if not generate_ai_note:
        # User toggled AI OFF: Zero OpenAI spend, queue for Blank Connect
        registry.log(job_id, "🔒 AI Note toggle is OFF. Queuing leads for 1-Tap Blank Connection (Zero OpenAI cost, zero bot pasting risk)...")
        now_iso = datetime.now(timezone.utc).isoformat()
        for lead in leads:
            update_lead_status(
                conn,
                lead_id=lead["id"],
                from_status=Status.QUALIFIED_ACTIVE,
                to_status=Status.QUEUED,
                triggered_by="portal_runner",
                reason="queued_blank_connect_safe",
                ab_variant="variant_b",
                personalized_note=None,
                note_char_count=0,
                queued_at=now_iso,
            )
            queued_count += 1
            registry.log(job_id, f"  ✓ Queued (Blank): {lead['founder_name']} at {lead['business_name']}")

        conn.commit()
        conn.close()
        registry.log(job_id, f"🎉 Successfully queued {queued_count} leads for Blank Outreach ($0.00 spent).")
        return {"queued": queued_count, "mode": "blank_connect", "cost_usd": 0.0}

    else:
        # User toggled AI ON: Generate personalized notes with strict batch size
        from stages.stage5_personalize import run_stage5
        registry.log(job_id, f"🤖 AI Note toggle is ON. Calling Azure OpenAI strictly for {len(leads)} leads...")
        conn.close()
        # run_stage5 takes batch_size directly
        res = run_stage5(batch_size=len(leads), ab_split=0.0)
        registry.log(job_id, f"AI Note generation complete: {res}")
        return res


def launch_pipeline_job(
    stage_id: str,
    batch_size: int = 15,
    generate_ai_note: bool = False,
    follower_filter: str = "ALL",
    cities: list[str] | None = None,
    queries: list[str] | None = None,
) -> str:
    """
    Non-blocking entry point to trigger a pipeline stage from the UI Portal.
    Returns: job_id
    """
    # Enforce strict bounds on batch size
    batch_size = max(1, min(batch_size, 50))

    job_id = registry.create_job(
        stage_name=stage_id,
        batch_size=batch_size,
        generate_ai_note=generate_ai_note,
    )

    thread = threading.Thread(
        target=_execute_stage_worker,
        args=(job_id, stage_id, batch_size, generate_ai_note, follower_filter, cities, queries),
        daemon=True,
    )
    thread.start()

    return job_id
