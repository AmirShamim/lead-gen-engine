# Pipeline Architectural Artifacts & Specifications

This directory contains the modular architecture specifications, data contracts, and execution roadmaps for the B2B Lead Generation and Enrichment Pipeline.

## Architectural Modules Directory

| # | Artifact Document | Stage / Scope | Description | Implementation File |
|---|---|---|---|---|
| **00** | [00-overview-and-architecture.md](./00-overview-and-architecture.md) | System Overview | Master decoupled architecture, state machine topology, and engineering philosophy | `orchestrator.py` |
| **01** | [01-database-and-state-machine.md](./01-database-and-state-machine.md) | Persistence Layer | SQLite WAL & PostgreSQL schemas, state transition ENUMs, and audit logging | `db.py` |
| **02** | [02-stage1-places-api-ingestion.md](./02-stage1-places-api-ingestion.md) | Stage 1: Ingestion | Google Places API (New) discovery, budget circuit breaker, and metro querying | `stages/stage1_places.py` |
| **03** | [03-stage2-website-enrichment.md](./03-stage2-website-enrichment.md) | Stage 2: Crawl & Enrichment | TLS-impersonated site crawling, JSON-LD / HTML parsing, and founder extraction | `stages/stage2_enrich.py` |
| **04** | [04-stage3-linkedin-resolution.md](./04-stage3-linkedin-resolution.md) | Stage 3: LinkedIn Resolution | Multi-engine search dorking (Serper / Google / Bing) and profile match scoring | `stages/stage3_resolve.py` |
| **05** | [05-stage4-activity-gate.md](./05-stage4-activity-gate.md) | Stage 4: Activity Gate | Deterministic recency validation, connection count check, zero-LLM filtering | `stages/stage4_activity.py` |
| **06** | [06-stage5-ai-personalization.md](./06-stage5-ai-personalization.md) | Stage 5: Personalization | Azure OpenAI GPT-4o-mini <250 char connection notes and anti-jargon enforcement | `stages/stage5_personalize.py` |
| **07** | [07-stage6-mobile-dispatch-queue.md](./07-stage6-mobile-dispatch-queue.md) | Stage 6: Dispatch Queue | Mobile-optimized FastAPI action queue for 6-minute daily native LinkedIn outreach | `stages/stage6_queue.py` |
| **08** | [08-execution-roadmap.md](./08-execution-roadmap.md) | Operational SOP | 4-Day build checklist, milestone metric tracking, and Phase 2 scale protocol | `orchestrator.py` |

---

## State Machine Overview

```
DISCOVERED ──► ENRICHED ──► LINKEDIN_RESOLVED ──► QUALIFIED_ACTIVE ──► QUEUED ──► SENT ──► ACCEPTED
     │              │               │                     │
     ▼              ▼               ▼                     ▼
DEAD_WEBSITE   NO_LEADERSHIP   NO_LINKEDIN          DORMANT_REJECT
```

## Decoupled Stage Execution

Each specification maps to a self-contained execution module in `stages/`:
- **Stage 1**: `python -m stages.stage1_places`
- **Stage 2**: `python -m stages.stage2_enrich`
- **Stage 3**: `python -m stages.stage3_resolve`
- **Stage 4**: `python -m stages.stage4_activity`
- **Stage 5**: `python -m stages.stage5_personalize`
- **Stage 6**: `python -m stages.stage6_queue`
- **Master Orchestrator**: `python orchestrator.py`
