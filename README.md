# B2B Lead Generation & Enrichment Pipeline

An always-on, decoupled 6-stage lead generation and enrichment pipeline with automated Google Places discovery, website crawling, LinkedIn resolution, deterministic activity gating, AI-powered personalization, and a mobile-optimized dispatch queue for safe, native outreach.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   SHARED DATABASE ENGINE                                    │
│                              (SQLite WAL / Azure PostgreSQL)                                │
│                                                                                             │
│  State Machine:                                                                             │
│  DISCOVERED ──► ENRICHED ──► LINKEDIN_RESOLVED ──► QUALIFIED_ACTIVE ──► QUEUED ──► SENT     │
│       │              │               │                     │                                │
│       ▼              ▼               ▼                     ▼                                │
│  DEAD_WEBSITE   NO_LEADERSHIP   NO_LINKEDIN          DORMANT_REJECT                         │
└───────┬──────────────┬───────────────┬─────────────────────┬──────────────┬─────────┬───────┘
        │              │               │                     │              │         │
        ▼              ▼               ▼                     ▼              ▼         ▼
   ┌─────────┐    ┌─────────┐     ┌─────────┐           ┌─────────┐    ┌─────────┐ ┌─────────┐
   │ STAGE 1 │    │ STAGE 2 │     │ STAGE 3 │           │ STAGE 4 │    │ STAGE 5 │ │ STAGE 6 │
   │ Places  │    │ Website │     │ Search  │           │ Activity│    │ AI Note │ │ Mobile  │
   │ Ingest  │    │ Crawl   │     │ Dorking │           │ Gate    │    │ Gen     │ │ Queue   │
   └─────────┘    └─────────┘     └─────────┘           └─────────┘    └─────────┘ └─────────┘
```

---

## Directory Structure

```
lead-pipeline/
├── artifacts/                  # Architecture specifications & data contracts (00–08)
│   ├── README.md               # Artifacts directory index & summary table
│   ├── 00-overview-and-architecture.md
│   ├── 01-database-and-state-machine.md
│   ├── 02-stage1-places-api-ingestion.md
│   ├── 03-stage2-website-enrichment.md
│   ├── 04-stage3-linkedin-resolution.md
│   ├── 05-stage4-activity-gate.md
│   ├── 06-stage5-ai-personalization.md
│   ├── 07-stage6-mobile-dispatch-queue.md
│   └── 08-execution-roadmap.md
├── data/                       # Local data & database persistence
│   ├── .gitkeep
│   └── pipeline.db             # Active SQLite database (WAL mode)
├── stages/                     # Decoupled stage worker modules
│   ├── __init__.py
│   ├── stage1_places.py        # Stage 1: Google Places API (New) discovery
│   ├── stage2_enrich.py        # Stage 2: TLS crawler & founder extraction
│   ├── stage3_resolve.py       # Stage 3: LinkedIn resolution (Serper/Google/Bing)
│   ├── stage4_activity.py      # Stage 4: Deterministic 90-day activity filter
│   ├── stage5_personalize.py   # Stage 5: Azure OpenAI GPT-4o-mini note generator
│   └── stage6_queue.py         # Stage 6: Mobile FastAPI action queue server
├── config.py                   # Central configuration, budgets, and constants
├── db.py                       # Database engine, schema DDL, and state transitions
├── orchestrator.py             # Master orchestrator for serial stage execution
├── requirements.txt            # Python dependencies
├── .env                        # Local environment variables (API keys & endpoints)
├── .env.example                # Example environment variable template
├── .gitignore                  # Git ignore rules
└── README.md                   # Project documentation
```

---

## Quickstart

### 1. Installation

```bash
# Clone or navigate to the repository
cd lead-pipeline

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate       # On Linux/macOS
.venv\Scripts\activate          # On Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your API credentials:

```bash
cp .env.example .env
```

Key configuration parameters:
- `DB_PATH`: Database path (default: `data/pipeline.db`)
- `GOOGLE_PLACES_API_KEY`: API key for Stage 1 Google Places search
- `SEARCH_PROVIDER`: Search engine provider for Stage 3 (`serper`, `google`, or `bing`)
- `SERPER_API_KEY`: API key for Serper.dev (Google Search)
- `AZURE_OPENAI_ENDPOINT`: Azure OpenAI resource endpoint for Stage 5
- `AZURE_OPENAI_KEY`: Azure OpenAI API key
- `AZURE_OPENAI_DEPLOYMENT`: Deployment model name (e.g., `gpt-4o-mini`)

---

## Execution Guide

### Master Orchestrator (`orchestrator.py`)

Run stages sequentially with real-time funnel reporting:

```bash
# Full pipeline with defaults
python orchestrator.py

# Preview Stage 1 without making external API calls
python orchestrator.py --stages 1 --dry-run

# Run enrichment and personalization on existing leads
python orchestrator.py --stages 2 3 4 5

# Custom cities and global quota expansion
python orchestrator.py --cities "Austin, TX" "Miami, FL" --per-city 100 --max-total 3000

# Batch size control for Stages 2–5
python orchestrator.py --batch-size 100
```

### Running Individual Stages

Each stage operates independently using the database as its message broker:

| Stage | Command | Prerequisite Status | Output Status |
|---|---|---|---|
| **Stage 1: Places** | `python -m stages.stage1_places` | N/A (New search) | `DISCOVERED` |
| **Stage 2: Website** | `python -m stages.stage2_enrich --batch-size 50` | `DISCOVERED` | `ENRICHED`, `DEAD_WEBSITE`, `NO_LEADERSHIP` |
| **Stage 3: LinkedIn** | `python -m stages.stage3_resolve --batch-size 50` | `ENRICHED` | `LINKEDIN_RESOLVED`, `NO_LINKEDIN` |
| **Stage 4: Activity** | `python -m stages.stage4_activity --batch-size 50` | `LINKEDIN_RESOLVED` | `QUALIFIED_ACTIVE`, `DORMANT_REJECT` |
| **Stage 5: AI Notes** | `python -m stages.stage5_personalize --batch-size 15` | `QUALIFIED_ACTIVE` | `QUEUED` |
| **Stage 6: Mobile Queue** | `python -m stages.stage6_queue` | `QUEUED` | `SENT` (via UI interaction) |

### Stage 6 Mobile Dispatch Queue

Start the local or cloud web server:

```bash
python -m stages.stage6_queue
```

Open `http://localhost:8000/queue` (or your cloud URL on mobile) to view today's ready leads, copy personalized notes with one tap, open direct LinkedIn profiles in the native app, and tap **Mark Sent**.

---

## Database & Funnel Management

View real-time lead counts by stage and cumulative API spend:

```bash
python -c "from db import get_connection, print_funnel_summary; conn = get_connection(); print_funnel_summary(conn); conn.close()"
```

To reinitialize or verify the schema:

```bash
python db.py
```

---

## Architectural Artifacts & Specs

All modular specifications and data contracts are consolidated in the [`artifacts/`](./artifacts/) folder:

- [Module 00: Overview & Master Architecture](./artifacts/00-overview-and-architecture.md)
- [Module 01: Database & State Machine](./artifacts/01-database-and-state-machine.md)
- [Module 02: Stage 1 — Google Places Ingestion](./artifacts/02-stage1-places-api-ingestion.md)
- [Module 03: Stage 2 — Website Enrichment & Founder Extraction](./artifacts/03-stage2-website-enrichment.md)
- [Module 04: Stage 3 — LinkedIn Search Engine Resolution](./artifacts/04-stage3-linkedin-resolution.md)
- [Module 05: Stage 4 — Deterministic Activity Gate](./artifacts/05-stage4-activity-gate.md)
- [Module 06: Stage 5 — AI Personalization Notes](./artifacts/06-stage5-ai-personalization.md)
- [Module 07: Stage 6 — Mobile Action Queue & Dispatch](./artifacts/07-stage6-mobile-dispatch-queue.md)
- [Module 08: Operational Execution Roadmap](./artifacts/08-execution-roadmap.md)
