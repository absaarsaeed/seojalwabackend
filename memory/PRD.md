# SEO Jalwa — Backend PRD

## Problem statement
Build the complete backend API for **SEO Jalwa**, an all-in-one AI growth platform that bundles AI article generation, social autopilot, AI-visibility scanning (ChatGPT / Perplexity / Gemini / Claude / Copilot), brand-voice training, multi-CMS auto-publish, billing, team management, public marketing pages, a WordPress plugin contract, and a full admin panel.

The original spec asked for Node + Express + PostgreSQL + Prisma. Per user choice (option 1a), the backend is built on **FastAPI + MongoDB** to match the platform.

## User personas
1. **Site owner / Marketer** — registers, connects a site (WordPress, Webflow, Ghost, HubSpot, Wix, Notion, Squarespace, Next.js, Shopify), generates SEO articles, schedules social posts, monitors AI-visibility scores and growth score.
2. **Agency** — manages many client sites, invites team members with site-level access.
3. **Admin (SEO Jalwa team)** — dashboard with MRR/users/funnel, full users CRUD, plan pricing edits, coupons, blog CMS, broadcast announcements, encrypted API-key vault, refunds, maintenance toggle.
4. **WordPress plugin** — polls `GET /api/plugin/articles/pending` every 15 min using `X-Jalwa-API-Key` header.

## Tech stack (delivered)
- FastAPI (Python) on supervisor at `0.0.0.0:8001`
- MongoDB via Motor (async driver) — DB name from `DB_NAME` env
- JWT user auth (`pyjwt`, access 15m / refresh 7d) + bcrypt password hashing (12 rounds)
- Session-cookie admin auth (2 h, 5/30-min lockout), hardcoded `jalwa` / `jalwaadmin`
- Fernet AES encryption for tokens / API keys at rest
- APScheduler crons + FastAPI BackgroundTasks for async jobs
- Pydantic v2 for validation; `RequestValidationError` returns standard envelope
- Swagger at `/api/docs`, OpenAPI at `/api/openapi.json`
- Real OpenAI (gpt-4o) via **Emergent Universal LLM Key** (`emergentintegrations`)
- Every other 3rd-party service (Resend, LemonSqueezy, Cloudflare R2, DataForSEO, GSC, all CMS publish adapters, all social publish adapters, Perplexity / Gemini / Claude / Copilot, Google OAuth, DALL-E 3) is **mocked** behind clean service interfaces in `services/mocks.py` with TODO comments showing the exact API calls to swap in when real keys are provided.

## What's been implemented (2026-05-18)

### Core
- Consistent `{success, data, message, pagination?}` envelope on success
- Consistent `{success:false, error, code, statusCode}` envelope on every error path (incl. 422 validation)
- MongoDB connection, DB-key cache loader (env fallback, 5-min TTL)
- bcrypt + JWT + admin sessions + Fernet encryption + in-memory rate limit
- APScheduler crons: daily article gen (06:00 UTC), weekly AI visibility (Mon 08:00), weekly growth score (Mon 07:00), daily GSC sync (02:00), hourly social publish
- `/health` and `/api/health` health checks

### Endpoints (≈120)
**Auth**: register / login / google / refresh / logout / verify-email / forgot-password / reset-password / me
**Sites**: list / create / get / update / soft-delete / verify-connection / connect{ghost, webflow, hubspot, wix, notion}
**Social**: accounts list/delete, auth-url, callback, posts CRUD + generate-from-article + approve + publish-now + analytics
**Articles**: list/get/generate(job)/job-status/update/publish/delete/reschedule/calendar
**Search terms**: list/add(batch)/delete/ai-suggest (real LLM)
**Article settings** per site
**AI Visibility**: scans/latest/scan(job)/competitors/simulate (5/hr rate limit)
**AI Writer**: brand-voice get/train, content/generate (real LLM), voice-score, library, delete
**Auto-publish**: connections, publish
**Analytics**: overview/articles/search-terms/top-pages/sync/gsc-connect
**Growth score**: get/calculate
**Team**: list/invite/update/remove/accept
**User**: profile/password/notifications/delete-account
**Billing**: plans/checkout/subscription/cancel/reactivate/invoices/webhook/apply-coupon
**WordPress plugin** (X-Jalwa-API-Key): verify/ping/articles-pending/confirm/track
**Public**: plans/blog/blog-slug/contact/ai-visibility-demo (5/hr rate limit)
**Admin auth**: login (5/30 lockout)/logout/verify
**Admin dashboard**: stats/activity
**Admin users**: list/get/plan/status/extend-trial/note/activity
**Admin plans**: list/create/update/delete (soft)
**Admin billing**: overview/transactions/refund
**Admin coupons**: CRUD
**Admin blog**: CRUD (auto-slug)
**Admin announcements**: send/history (target-plan filter)
**Admin analytics**: users/revenue/modules/funnel
**Admin API keys**: list (masked) / supported / create / update / test (real OpenAI test)
**Admin settings**: get/update/change-admin-password

### Background jobs
- `run_article_generation`: keyword research → LLM article → image → optional auto-publish → social-post job chain
- `run_social_post_generation`: per-platform captions + images + scheduling
- `run_ai_visibility_scan`: 5-model query + scoring + recommendations + growth-score recalc trigger
- `run_growth_score`: weighted 30/25/25/20 of AI / SEO / Social / Traffic
- `run_gsc_sync`, `run_brand_voice_training`

### Seed (`seed.py`)
- 3 default plans: **Starter $79**, **Growth $199**, **Agency $499** with feature flags per spec
- Admin credentials `jalwa` / `jalwaadmin`
- 21 default `api_configs` rows (inactive, ready to be populated by admin)

### Tests
- `backend_test.py` covers 57 features — 100% pass

## Prioritised backlog (P0/P1/P2)

### P0 — Wire real 3rd-party credentials
1. Real **Resend** transactional emails (replace `services/mocks.send_email`)
2. Real **LemonSqueezy** checkout + webhook signature verify
3. Real **Cloudflare R2** uploads + signed URLs
4. Real **DataForSEO** keyword research

### P1 — Activate remaining AI providers
5. Replace mock Perplexity / Gemini / Claude / Copilot with real SDKs in `services/mocks.query_ai_models`
6. Real DALL-E 3 image generation in `services/llm.generate_image`
7. Real Google OAuth verify in `services/mocks.verify_google_token`
8. Real GSC OAuth + `searchanalytics().query()`

### P1 — CMS / Social publish adapters
9. WordPress (plugin already polls — backend side done)
10. Webflow / Ghost / HubSpot / Wix / Notion REST publishers
11. Instagram / Facebook / LinkedIn / Twitter / Pinterest / YouTube publishers

### P2 — Hardening
12. Externalise rate limiter to Redis (currently in-memory, single-worker only)
13. Plan-limit enforcement middleware (count articles/posts/scans per month vs plan)
14. Soft-delete cron sweep / GDPR delete worker
15. Audit log middleware writing to `admin_activity` collection
16. WebSocket job-status streaming (currently polled)

## Files of interest
- `/app/backend/server.py` — main app
- `/app/backend/seed.py` — idempotent seed
- `/app/backend/core/` — response, security, encryption, deps, rate-limit, scheduler, database
- `/app/backend/services/` — llm (real), api_keys (DB cache), mocks (all stubs), jobs
- `/app/backend/routers/` — 16 user routers + 10 admin routers
- `/app/memory/test_credentials.md` — admin + test-user creds
