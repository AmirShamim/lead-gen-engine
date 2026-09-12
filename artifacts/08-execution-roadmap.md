# Module 08: Operational Execution Roadmap & The 100-Connection Milestone

> **Sprint Objective:** Build and deploy the cloud-enrichment pipeline in 4 days. Execute a disciplined 6-minute daily mobile outreach rhythm until achieving **100+ accepted connections**.

---

## 1. Day-by-Day Implementation Checklist

```
DAY 1: Schema Setup & Places API Ingestion
[ ] Initialize SQLite database with Write-Ahead Logging (`data/pipeline.db`).
[ ] Execute DDL from [Module 01](./01-database-and-state-machine.md) to create leads and api_calls tables.
[ ] Implement Google Places API (New) client with Basic FieldMask ([Module 02](./02-stage1-places-api-ingestion.md)).
[ ] Integrate budget circuit breaker.
[ ] Run test queries across target cities (e.g., Austin, Miami, Denver).
[ ] Target: 400+ fresh businesses with status = 'DISCOVERED'.

DAY 2: Live Website Enrichment & LinkedIn Resolver
[ ] Implement curl_cffi crawler with Chrome124 TLS impersonation ([Module 03](./03-stage2-website-enrichment.md)).
[ ] Build JSON-LD, meta, and regex founder extraction logic.
[ ] Set up Serper / Google / Bing Search dorking client ([Module 04](./04-stage3-linkedin-resolution.md)).
[ ] Implement candidate profile match scoring (>= 50 pts threshold).
[ ] Process Day 1 batch: verify 150+ leads reach status = 'LINKEDIN_RESOLVED'.

DAY 3: Deterministic Activity Gate & Azure OpenAI Notes
[ ] Implement activity inspector and regex timestamp parser ([Module 05](./05-stage4-activity-gate.md)).
[ ] Test connection count gate (>= 100) and drop dormant accounts.
[ ] Set up Azure OpenAI GPT-4o-mini client ([Module 06](./06-stage5-ai-personalization.md)).
[ ] Enforce <250 character constraint and zero-jargon post-validation.
[ ] Target: 50+ hyper-personalized leads with status = 'QUEUED'.

DAY 4: Cloud Deployment & Mobile Launch
[ ] Deploy code & database to an Azure VM or Container App.
[ ] Set up cron job to run enrichment pipeline nightly at 02:00 UTC.
[ ] Run FastAPI mobile queue server ([Module 07](./07-stage6-mobile-dispatch-queue.md)) on port 8000.
[ ] Open mobile queue on your phone (`http://your-host:8000/queue`).
[ ] LAUNCH: Send your first 15 connections via official LinkedIn mobile app!
```

---

## 2. The 100-Connection Milestone Metric Tracker

Do **not** start Phase 2 (browser automation, client packaging) until you fill this table:

| Metric Name | Formula / Query | Target | Why It Matters |
|---|---|---|---|
| **Total Invites Sent** | `COUNT(*) WHERE status = 'SENT'` | **250–350** | Base volume over ~4–5 weeks |
| **Accepted Connections** | `COUNT(*) WHERE status = 'ACCEPTED'` | **100+** | **The Phase 2 Unlock Gate** |
| **Acceptance Rate** | `Accepted / Sent` | **30% – 40%** | Proves activity filter & note quality |
| **Ghost Acceptances** | Accepted but 0 replies after 7 days | **40% – 50%** | Baseline for follow-up message testing |
| **Direct Reply Rate** | Replied directly to connection note | **15% – 25%** | Proves offer resonance |
| **Discovery Calls Booked** | Meetings confirmed | **6 – 12** | Validates Trojan Horse offer |

---

## 3. Phase 2 Unlock Protocol (What Happens Next)

Once `Accepted >= 100`:
1. **Analyze Best-Performing Notes:** Export acceptance rates by note length and angle.
2. **Automate Dispatch:** Transition mobile sending to Playwright/Camoufox on residential proxy.
3. **Multi-Tenant SaaS Transition:** Move database to Azure PostgreSQL Flexible Server and create client license keys.
4. **Client Dashboard:** Package the React + Vite frontend for agency clients.
