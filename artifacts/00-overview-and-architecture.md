# B2B Lead Pipeline — Master Architecture & Modular System Design

> **System Vision:** An always-on, decoupled 6-stage lead generation and enrichment pipeline running on Azure cloud compute ($5,000 credit subsidy), feeding a daily high-yield action queue to your mobile phone for safe, native LinkedIn outreach (0% ban risk).
> 
> **Core Engineering Philosophy:** Complete decoupling. Each stage reads from the database using a strict input contract, performs its task, and updates state via an output contract. **Any stage can be executed, tested, or modified completely in isolation.**

---

## 1. Modular System Topology

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

## 2. Module Index & File Directory

All modular architectural artifacts and specifications are stored within `artifacts/`:

| Module Guide | Primary Function | Core Technology | Standalone Testable? |
|---|---|---|---|
| [01-database-and-state-machine.md](./01-database-and-state-machine.md) | Relational schema, state ENUMs, transition audit log | SQLite WAL / PostgreSQL | Yes |
| [02-stage1-places-api-ingestion.md](./02-stage1-places-api-ingestion.md) | Ground-truth business discovery with budget caps | Google Places API (New) | Yes |
| [03-stage2-website-enrichment.md](./03-stage2-website-enrichment.md) | Live site crawling & confidence-scored founder extraction | `curl_cffi`, BeautifulSoup | Yes |
| [04-stage3-linkedin-resolution.md](./04-stage3-linkedin-resolution.md) | Search engine dorking to resolve personal LinkedIn URLs | Serper / Google / Bing API | Yes |
| [05-stage4-activity-gate.md](./05-stage4-activity-gate.md) | Deterministic activity filter (Zero-LLM cost) | Regex timestamps, DOM parsing | Yes |
| [06-stage5-ai-personalization.md](./06-stage5-ai-personalization.md) | Hyper-personalized, <250 char connection notes | Azure OpenAI GPT-4o-mini | Yes |
| [07-stage6-mobile-dispatch-queue.md](./07-stage6-mobile-dispatch-queue.md) | Daily 10–15 lead mobile action queue & 6-min workflow | Lightweight FastAPI web app | Yes |
| [08-execution-roadmap.md](./08-execution-roadmap.md) | 4-Day build checklist & the 100-connection milestone | Step-by-step SOP | Yes |

---

## 3. Strict Decoupling Rules Between Modules

To ensure no module interferes with another:

1. **No Direct Inter-Module Function Calls:** Stage 1 does *not* call Stage 2. Stage 2 does *not* call Stage 3.
2. **Database as the Sole Message Broker:** Each module is a worker that:
   * Polls the database for records in a specific prerequisite state (e.g., Stage 2 only queries `WHERE status = 'DISCOVERED'`).
   * Processes the batch.
   * Commits the updated state (`ENRICHED`, `DEAD_WEBSITE`, or `NO_LEADERSHIP`).
3. **Idempotent Execution:** Re-running any stage on the same data multiple times causes zero duplicate records or invalid state transitions.
4. **Independent CLI Entrypoints:** Each stage has its own CLI command (e.g., `python -m stages.stage1_places --help` or `python orchestrator.py --stages 1`).
5. **Centralized Data & Artifacts:** Runtime databases reside in `data/pipeline.db` and design specifications reside in `artifacts/`.
