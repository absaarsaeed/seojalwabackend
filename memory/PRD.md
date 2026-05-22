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

## Phase 1 — 2026-05-19

**Status: COMPLETE. 77/79 backend tests pass (zero regressions vs Phase 0 baseline of 57/57).**

### Real integrations wired
- **SendGrid** (replaced Resend everywhere) — 8 transactional HTML templates: welcome, verify, password_reset, article_published, weekly_digest, team_invite, announcement, payment_failed
- **OpenAI GPT-4o article generation** — strict-JSON output, deterministic 0-100 SEO score, brand-voice context injection
- **DALL-E 3** hero images (1792x1024) → re-uploaded to Cloudflare R2
- **Cloudflare R2** via boto3 (`upload_file`, `delete_file`, `get_signed_url`, `download_to_r2`)
- **WordPress REST** real publisher (Yoast meta + featured-media upload)
- **AI Visibility 5-model scan**: real OpenAI (gpt-4o-mini), Perplexity, Gemini (1.5-flash), Claude (Haiku); Copilot derived (no public API)
- **Google Search Console** OAuth + searchanalytics().query() with real article-clicks back-fill
- **Brand voice training** — URL → BeautifulSoup → GPT-4o style profile (tone/formality/playfulness/technicality/sentenceLength/vocabulary/characteristicPhrases/thingsToAvoid/writingPersona)
- **Real Growth Score** algorithm (AI 30% / SEO 25% / Social 25% / Traffic 20%)
- **New GSC routes**: GET /api/analytics/gsc/connect, GET /api/analytics/gsc/callback
- **Weekly digest cron** (Monday 08:05 UTC)
- **Admin api-key /test** now does real pings to OpenAI / SendGrid / R2 / Perplexity / Gemini / Anthropic / Google with `latency_ms`

### Still mocked (Phase 2 backlog)
- LemonSqueezy checkout/webhook/refund
- DataForSEO keyword research
- Webflow/Ghost/HubSpot/Wix/Notion CMS publishers
- All 6 social publishers (IG/FB/LinkedIn/Twitter/Pinterest/YouTube)
- Google OAuth login (POST /api/auth/google) — GSC OAuth is real
- Microsoft Copilot scan (no public API — derived)

## Master launch readiness — 2026-05-21

**Status: COMPLETE. 29/29 bash smoke + 23/23 pytest (master_launch + e2e) PASS.**

### Backend deliverables for the 20-part "Master launch readiness" prompt
- **Part 1 — Auto site analysis**: `services/site_analyzer.py` fetches homepage + WP posts + categories, asks GPT-4o for niche/audience/style, persists `article_settings`, suggests 10 search terms, builds `categoryMapping`, marks site `analyzed=true`. Triggered from `POST /api/plugin/verify` and `POST /api/sites/{id}/verify-connection`.
- **Part 2 — Trial article pre-generation**: `services/trial.py::setup_trial_articles()` queues `trial_days // 4` (3-7) SCHEDULED articles for the daily 6 AM cron. Chained from the analyser.
- **Part 2b — Plan article batch**: `setup_plan_articles()` triggered when admin upgrades a user (admin/users.py:356).
- **Part 4 — Intelligent category selection**: `llm.pick_category(topic, mapping)` chooses the best WP category from `site.categoryMapping` (exact → token-overlap). Wired into `jobs.run_article_generation` so every published article carries `wordpressCategoryId` + `wordpressCategoryName`. WordPress publisher injects `categories: [id]` payload.
- **Part 5 — Internal & external AI linking**: `llm.resolve_article_links(content, topic, candidates)` replaces `[INTERNAL_LINK: anchor]` placeholders with `<a href>` to existing published articles (token overlap match on title), and `[EXTERNAL_LINK: anchor]` with GPT-suggested authoritative URLs (wikipedia/.gov/.edu/industry sites). Unresolved placeholders strip to plain text so published HTML is always clean.
- **Part 7 — GSC daily auto-sync cron**: APScheduler `cron_daily_gsc_sync` at 02:00 UTC iterates every site → pulls 30 days → updates per-article clicks/impressions/CTR/avgPosition.
- **Part 10 — White-label publishing**: `wordpress.publish_article` reads the user's plan; if `plan.whiteLabel=False` it appends a small "Powered by SEO Jalwa" footer. Agency plan stays clean.
- **Part 11 — `cmsConnections` → `websiteConnections` rename**: Seed/list/POST/PUT now write BOTH keys; idempotent `_migrate_plan_field_rename()` runs every startup; all 3 list endpoints (`/api/plans`, `/api/billing/plans`, `/api/admin/plans`) backfill both keys for legacy rows.

### Bugs fixed in this session
- **GSC 500**: `routers/analytics.py` was `await`-ing sync functions `_gsc.build_authorize_url()` and `_gsc.exchange_code()`, crashing when Google OAuth was unconfigured. Removed the `await`; endpoint now returns proper `400 GSC_NOT_CONFIGURED`.

### Tests added
- `/app/backend/tests/test_master_launch.py` — 8 unit tests (`pick_category`, `resolve_article_links`, `_best_internal_match`)
- `/app/backend/tests/test_master_launch_e2e.py` — 15 end-to-end tests (added by testing agent)
- `/app/backend/tests/test_iteration4_fixes.py` — 11 e2e tests covering the 11-point dummy-data audit (FIX 1/2/2b/3/4/5/6/7/8/10/11)

## Phase 1 fix batch — 2026-05-22 (Iteration 5)

**Status: COMPLETE. 13/13 iteration5 + 29/29 smoke + 23/23 master_launch + 10/11 iteration4 (1 known 429 flake) PASS.**

Ten endpoint fixes shipped in one pass:
- **FIX 1 — GSC OAuth**: `GET /api/analytics/gsc/connect` now loads `client_id` / `client_secret` from `config_service` first (admin-saved DB values), env vars second, and builds the full Google authorize URL inline. Returns `400 GSC_NOT_CONFIGURED` when nothing is set; returns a real `accounts.google.com/o/oauth2/v2/auth?…` URL once the admin configures the keys via `/api/admin/api-keys/google_oauth`.
- **FIX 2 — verify-connection trusts DB**: `POST /api/sites/{id}/verify-connection` returns `connected:true` immediately when `site.wordpressConnected` is already true (set by the plugin's verify call), without making a redundant HTTP probe. Avoids false negatives on staging URLs / firewalls.
- **FIX 3 — AI visibility simplified**: `services/ai_visibility.py::run_scan` rewritten to ChatGPT-only with 5 brand-mention queries. Returns `overallScore`, `visibilityStatus` (`VISIBLE` / `PARTIAL` / `NOT_VISIBLE`), `visibilityMessage`, `queriesRun`, `mentionsFound`, `results[5]`, `recommendations`. Legacy 5-model fields kept for back-compat.
- **FIX 4 — Resend fallback**: `services/email.py::send_email` falls back to `onboarding@resend.dev` sender when no verified domain `from_email` is configured, with a warning log. `email_logs` already record full `errorMessage` from the Resend API.
- **FIX 5 — Real admin activity**: `GET /api/admin/dashboard/activity` combines 5 real DB sources — recent signups, `admin_audit_log`, `subscriptions` status changes, published articles, and `user_activity_log` SITE_CONNECTED events. No dummy data.
- **FIX 6 — Real admin users**: verified `GET /api/admin/users` returns DB users only; no fallback.
- **FIX 7 — Rich audit diff**: `PUT /api/admin/users/{id}/subscription` audit log now includes `changes.plan.{from,to}` (human-readable plan names) in addition to `changes.planId.{from,to}`, and `metadata.userEmail`.
- **FIX 8 — CMS→Website**: confirmed full coverage (all 3 plan endpoints expose both `cmsConnections` and `websiteConnections` with equal values; no "CMS Connections" labels in the codebase).
- **FIX 9 — Two images per article**: new `llm.generate_inline_image()` produces a square DALL-E 3 inline image alongside the existing 16:9 hero. `llm.insert_inline_image()` deterministically embeds the inline `<figure><img/></figure>` after the 2nd `<h2>` (fallback: 1st `<h2>` then first `</p>`). Article documents now persist both `featuredImageUrl` and `inlineImageUrl`.
- **FIX 10 — Persistent onboarding**: `routers/user.py` exposes `GET /api/user/onboarding` and `PUT /api/user/onboarding`. State is stored on `user.onboarding` and auto-flips steps to `true` as the underlying data appears (site connected, article settings exist, search terms added, scan run). `GET /api/auth/me` includes the merged `onboarding` object.

### Tests added
- `/app/backend/tests/test_iteration5_fixes.py` — 13 e2e tests (incl. 2 slow tests for the GPT-4o scan + 2-image article gen)

## Phase 2 — Free plan + dummy checkout + cross-site quota (Iteration 6) — 2026-05-22

**Status: COMPLETE. 29/29 smoke + 9/9 iteration6 + 8/8 master_launch unit PASS.**

Ten parts shipped + two critical bugs surfaced & fixed in the same iteration:
- **Part 1 — Free plan seeded**: 4 plans now in `seed.py` (Free, Starter, Growth, Agency) with `slug`, `isFree`, `order`, nested `features` map (each feature has `{enabled, value}`). Existing plans backfilled on every startup (no destructive overwrite).
- **Part 2 — Plan feature control**: `routers/admin/plans.py` accepts nested `features` and mirrors to flat top-level fields via `_sync_features_flat()`. **Bug fix:** `PUT /api/admin/plans/{id}` now uses dot-notation `$set` per feature key so a partial features payload doesn't wipe other features.
- **Part 3 — Cross-site article quota**: `core/plan_limits.check_article_limit` counts across ALL user's sites for the month. New `GET /api/user/quota` returns per-site breakdown with auto-equal distribution default. New `PUT /api/user/quota/sites/{id}` validates `sum(quotas) ≤ planTotal`.
- **Part 4 — Free plan on registration**: `routers/auth.py::register` now auto-assigns the Free plan as `ACTIVE` (no trial end date). Falls back to TRIALING the cheapest plan only if no Free plan exists.
- **Part 5 — Plan selection page**: `GET /api/plans/selection` formats plans with `displayValue` per feature ("60 articles/month", "3 websites", "20 AI scans/month"), `cta` text, `highlighted` flag (true for Growth).
- **Part 6 — Dummy checkout**: `POST /api/billing/checkout` creates a session with original/discounted/final prices. `POST /api/billing/checkout/{id}/complete` accepts any card, upgrades the subscription, creates a PAID invoice, records audit + notification + email + `setup_plan_articles` trigger. Idempotent (second `/complete` returns 409 SESSION_CONSUMED).
- **Part 7 — Real coupons**: `POST /api/billing/validate-coupon` with `{code, planId, billingInterval}` returns `{valid, code, discount.{type,value,amount}, originalPrice, finalPrice}`. Coupons applied during `/checkout` and `usedCount` incremented on `/complete`. **Bug fix:** coupon type vocabulary canonicalised — both `PERCENT` and `PERCENTAGE` accepted on input, both stored as `PERCENT`. Migration `_migrate_coupon_type` heals legacy rows on startup.
- **Part 8 — Site auto-analysis on first connect**: already implemented in previous iteration (`services/site_analyzer.py` + plugin verify hook).
- **Part 9 — Admin upgrade triggers articles**: `PUT /api/admin/users/{id}/subscription` now triggers `setup_plan_articles` on any planId change (not just TRIALING→ACTIVE), skipped when target plan is Free.
- **Self-healing migrations**: `seed.py` now backfills missing inner `features` keys on existing plan docs and canonicalises coupon types — protects against any future schema drift.

### Tests added
- `/app/backend/tests/test_iteration6_phase2.py` — 9 e2e tests covering PART 1-9 (9/9 passing post-fix)

**Status: COMPLETE. 29/29 smoke + 23/23 master-launch + 11/11 iteration-4 PASS.**

## Real-data audit — 2026-05-22 (Iteration 4)

Eleven endpoints/flows fixed so the frontend never sees dummy data:
- **FIX 1 — `GET /api/admin/users/{userId}`**: response now includes populated `subscription.plan` (full plan dict, not just planId), `usage.{articlesThisMonth, socialPostsThisMonth, aiScansThisMonth, teamSeatsUsed}` and a new `stats.{totalArticles, totalClicks, totalScans, growthScore}` block.
- **FIX 2 — `GET /api/dashboard/overview`**: reshaped with `growthScore.{score, change, breakdown.{aiVisibility, seoContent, socialConsistency, trafficTrend}}`, `nextScheduledArticle`, `topPerformingArticle`, top-level booleans `hasConnectedSite/hasGeneratedArticle/hasRunScan`, `trial.{isTrialing, daysRemaining, trialEndsAt}`, plus `metrics.{articlesPublished, avgPosition, aiVisibilityScore}`.
- **FIX 2b — Empty state**: dashboard returns 200 (not 404) when user has no site yet — `site:null`, all metrics 0, single onboarding recommendation.
- **FIX 3 — `GET /api/growth-score`**: always returns valid `{score, breakdown, history, trend, change, message}`. Zero values + onboarding message on first call.
- **FIX 5 — `GET /api/analytics/overview`**: reshaped with `gscConnected`, `metrics`, `trend`, `topArticles[10]`, `topQueries[10]`. Returns `message: "Connect Google Search Console..."` when `gscConnected:false`.
- **FIX 6 — GSC OAuth**: `/api/analytics/gsc/connect` now correctly returns `400 GSC_NOT_CONFIGURED` instead of `500 await-on-None`.
- **FIX 8 — Activity logging**: `SITE_ADDED` (sites POST), `ARTICLE_GENERATED`, `ARTICLE_PUBLISHED` (jobs.run_article_generation), `AI_SCAN_RUN` (jobs.run_ai_visibility_scan) now persist to `user_activity_log` with `{title, link}` metadata for the activity feed.
- **FIX 10 — Admin subscription update**: `PUT /api/admin/users/{userId}/subscription` now (a) returns populated `subscription.plan`, (b) creates an in-app `SUBSCRIPTION_RENEWED` notification, (c) writes a `SUBSCRIPTION_UPGRADED` entry to user_activity_log, in addition to the pre-existing email + audit log + plan-articles trigger.
- **FIX 4 / 7 / 11**: already correct in previous iteration — verified by tests (latest scan returns null cleanly, calendar groups by date, cascade delete returns counts and removes user).

### Known minor issues (carried from Phase 0)
1. In-memory rate-limit/admin-lockout key on `request.client.host` (ingress IP). Move to Redis + honour `X-Forwarded-For` for multi-pod scaling.
2. `RequestValidationError` handler short-circuits before rate-limit dep on the public AI-visibility demo. Either move rate-limit to a higher-priority Request-only dep, or accept and document.

### New env vars (also in `.env.example`)
SENDGRID_API_KEY, SENDGRID_FROM_EMAIL, SENDGRID_FROM_NAME,
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_URL,
GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI,
PERPLEXITY_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY.

`RESEND_API_KEY` is gone.

### New pip packages
sendgrid, boto3, google-auth, google-auth-oauthlib, google-auth-httplib2, google-api-python-client, google-generativeai, anthropic, beautifulsoup4, httpx.
