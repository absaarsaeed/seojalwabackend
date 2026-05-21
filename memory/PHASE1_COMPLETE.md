# SEO Jalwa Backend — Phase 1 Completion Report

Date: 2026-05-19 (revised after Phase 1.2 WordPress Plugin delivery)

This document inventories every change made in Phase 1 and Phase 1.1, every endpoint
that is now wired to a **real** integration, every endpoint that remains
mocked (and why), the new env vars / pip packages, and any breaking changes
the frontend builder needs to know about.

---

## 0 · Phase 1.1 — Admin API Key Management (NEW)

The website owner can now add, update, and test ALL third-party API keys
from the admin panel at `domain.com/adminpanel/api-keys` **without ever
touching Railway, `.env`, or any code**.

### How it works
- All keys are stored encrypted in MongoDB (`api_configs` collection).
- **DB first, env fallback**: every service call resolves keys through
  `services.config.config_service.get_value(...)` which checks the DB,
  caches the decrypted value for 5 minutes, and only falls back to the
  matching environment variable if no DB value is set.
- Saving a key via the admin panel **invalidates the cache immediately**, so
  changes take effect on the very next call (no restart, no deploy).
- Display values are masked to last-4 (e.g. `••••••••••••1234`).

### Service catalogue (13 services, 7 sections)

| Section | Service key | Fields |
|---|---|---|
| AI Models | `openai` | api_key |
| AI Models | `anthropic` | api_key |
| AI Models | `gemini` | api_key |
| AI Models | `perplexity` | api_key |
| Email | `sendgrid` | api_key + from_email |
| SEO & Keywords | `dataforseo` | login + password |
| File Storage | `cloudflare_r2` | account_id + access_key_id + secret_access_key + bucket_name + public_url |
| Google Services | `google_oauth` | client_id + client_secret |
| Social Media OAuth Apps | `meta` | app_id + app_secret |
| Social Media OAuth Apps | `linkedin` | client_id + client_secret |
| Social Media OAuth Apps | `twitter` | client_id + client_secret |
| Social Media OAuth Apps | `pinterest` | app_id + app_secret |
| Payments | `lemonsqueezy` | api_key + store_id + webhook_secret |

The full catalogue (label, description, step-by-step instructions, signup
URL, notes) lives in `services/api_catalog.py` and is returned verbatim by
the admin endpoints so the frontend doesn't have to duplicate any of it.

### Admin endpoints

| Method | Path | What it returns |
|---|---|---|
| `GET` | `/api/admin/api-keys` | List of all 13 services — each entry has `{key, label, section, description, fields:[{name,label,type,placeholder,required,value(masked),isSet}], status, last_tested, test_status, instructions:{title,steps[],url,note}}`. |
| `GET` | `/api/admin/api-keys/supported` | Plain list of slug keys. |
| `GET` | `/api/admin/api-keys/{key}` | Single service detail (same shape as above). |
| `PUT` | `/api/admin/api-keys/{key}` | Body `{fields:{...}}` — encrypts and saves. Returns `{saved:true, key, masked_values, status}`. **Cache invalidated immediately.** |
| `POST` | `/api/admin/api-keys/{key}/test` | Runs a real per-service connection test. Returns `{success, message, latency_ms, tested_at}`. Updates `lastTestedAt` + `testStatus` on the record. |

### What each `/test` endpoint actually does (real calls)

| Service | Test action |
|---|---|
| `openai` | `client.chat.completions.create(model='gpt-4o-mini', max_tokens=5)` ping |
| `anthropic` | `client.messages.create(model='claude-3-haiku-20240307')` ping |
| `gemini` | `model.generate_content_async('Say OK')` ping |
| `perplexity` | POST `/chat/completions` with `llama-3.1-sonar-small-128k-online` |
| `sendgrid` | GET `/v3/user/account` |
| `cloudflare_r2` | `boto3.list_objects_v2(MaxKeys=1)` against the bucket |
| `dataforseo` | POST `/v3/appendix/user_data` with Basic auth |
| `lemonsqueezy` | GET `/v1/stores` |
| `google_oauth`, `meta`, `linkedin`, `twitter`, `pinterest` | Presence-check only — OAuth apps cannot be auto-tested; the test confirms required fields are present and returns success with the message "will be validated when a user connects their account". |

### Consumer refactor
Every service now reads keys through `config_service` instead of
`os.environ.get()` directly:

- `services/llm.py` — `_api_key_async()` (DB → env)
- `services/email.py` — `send_email` reads `sendgrid.api_key` and `sendgrid.from_email`
- `services/storage.py` — `_r2_config()` builds the boto3 client per call
- `services/ai_visibility.py` — `_query_perplexity / gemini / claude / test_*`
- `services/gsc.py` — `_gsc_config()` powers `build_authorize_url` and `exchange_code`

The env vars in `.env` still work as the **bootstrap fallback**. So on a
fresh Railway deploy, you can populate values via env once and then move
everything into the admin panel later without downtime.

---

## 1 · Endpoints fully implemented with real integrations

### Email (SendGrid)
- `POST /api/auth/register` → real `welcome_email`
- `POST /api/auth/forgot-password` → real `password_reset`
- `POST /api/team/invite` → real `team_invite`
- `POST /api/admin/announcements` → real `announcement_email` to each recipient
- Article generation job → real `article_published` after CMS publish
- **Monday 08:05 UTC cron** → `weekly_digest`
- Payment-failed webhook path → real `payment_failed`

### Article generation (OpenAI GPT-4o + DALL-E 3 + R2 + WordPress REST)
- `POST /api/articles/generate` (queued job)
- Pipeline: load `ArticleSettings` + `BrandVoice` → call **GPT-4o** with
  strict-JSON spec prompt → deterministic SEO score → optional **DALL-E 3**
  hero image → re-upload to **Cloudflare R2** → auto-publish to
  **WordPress REST** if connected → send `article_published` email.
- Article record gains the new fields: `metaTitle`, `metaDescription`,
  `excerpt`, `keyTakeaways[]`, `faqSchema[]`, `suggestedTags[]`,
  `estimatedReadTime`, `seoScore` (deterministic 0-100).

### DALL-E 3 hero images
- `services/llm.generate_hero_image` with the spec prompt template (16:9,
  no text, modern professional style).
- Re-uploaded via `services/storage.download_to_r2`.

### Cloudflare R2 (boto3 S3)
- `upload_file`, `delete_file`, `get_signed_url`, `download_to_r2`,
  `test_r2`.

### WordPress publishing
- Real `wp-json/wp/v2/posts` create with Yoast meta + featured-media
  upload via `wp-json/wp/v2/media`.

### AI Visibility 5-model scan
- Real OpenAI (`gpt-4o-mini`), Perplexity (`llama-3.1-sonar-small-128k-online`),
  Gemini (`1.5-flash`), Claude (`claude-3-haiku-20240307`). Copilot derived
  ± variance (no public API).
- 20 brand queries from GPT-4o, sentiment detection, weighted overall
  (30/25/20/15/10), 5 GPT-4o recommendations.

### Brand voice training
- URL fetched + visible text extracted via BeautifulSoup.
- GPT-4o profile (`tone, formality, playfulness, technicality,
  sentenceLength, vocabulary, characteristicPhrases, thingsToAvoid,
  writingPersona`) stored on `BrandVoice.styleProfile`.
- Profile injected as system context in every future article + content
  generation.

### Real Growth Score
- AI 30 % · SEO 25 % · Social 25 % · Traffic 20 %. Triggered after every AI
  scan + weekly cron + on demand.

### Google Search Console (real OAuth 2.0)
- **NEW** `GET /api/analytics/gsc/connect` → authorize URL
- **NEW** `GET /api/analytics/gsc/callback` → token exchange + redirect
- `POST /api/analytics/sync` → real `searchanalytics().query()` and
  back-fills `Article.clicks/impressions/ctr/avgPosition`.

### Daily article cron (06:00 UTC)
- Skips inactive subscriptions, missing CMS connections, already-scheduled
  sites; picks next PENDING SearchTerm or GPT-4o-suggested topic; runs the
  full real pipeline above.

---

## 2 · Endpoints still mocked (and why)

| Area | Why | Where to replace |
|---|---|---|
| LemonSqueezy checkout/refund (the **webhook receiver** parses real payloads but the **signature verification stub** still returns true) | Awaiting real `LEMONSQUEEZY_API_KEY` + `LEMONSQUEEZY_WEBHOOK_SECRET` | `services/mocks.create_checkout`, `verify_lemonsqueezy_signature`, `lemonsqueezy_refund` |
| DataForSEO keyword research | Awaiting credentials | `services/mocks.keyword_research` |
| 5 non-WordPress CMS publishers (Webflow, Ghost, HubSpot, Wix, Notion) | Phase 1 only covered WordPress per spec | `services/mocks.publish_to_cms` |
| 6 social publishers (IG, FB, LinkedIn, Twitter, Pinterest, YouTube) | OAuth apps still in review | `services/mocks.publish_social_post`, `get_social_oauth_url`, `social_exchange_code` |
| Google OAuth login (`POST /api/auth/google`) | GSC OAuth IS real; login flow stub remains | `services/mocks.verify_google_token` |
| Microsoft Copilot in AI Visibility | No public API | derived score in `services/ai_visibility._run_model_scan` |

---

## 3 · Environment variables

Every key listed here is **optional** because the admin panel can configure
them at runtime. Set them in Railway only for initial bootstrap.

```
# OpenAI (GPT-4o + DALL-E 3)
OPENAI_API_KEY=

# Anthropic / Gemini / Perplexity
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
PERPLEXITY_API_KEY=

# Email
SENDGRID_API_KEY=
SENDGRID_FROM_EMAIL=hello@seojalwa.com
SENDGRID_FROM_NAME=SEO Jalwa

# DataForSEO
DATAFORSEO_LOGIN=
DATAFORSEO_PASSWORD=

# Cloudflare R2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=seojalwa-assets
R2_PUBLIC_URL=

# Google OAuth (Search Console + YouTube)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://api.seojalwa.com/api/analytics/gsc/callback

# Social platforms
META_APP_ID=
META_APP_SECRET=
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
TWITTER_CLIENT_ID=
TWITTER_CLIENT_SECRET=
PINTEREST_APP_ID=
PINTEREST_APP_SECRET=

# LemonSqueezy
LEMONSQUEEZY_API_KEY=
LEMONSQUEEZY_STORE_ID=
LEMONSQUEEZY_WEBHOOK_SECRET=
```

`RESEND_API_KEY` is **removed** from `.env.example`.

### Pip packages added in Phase 1
`sendgrid · boto3 · google-auth · google-auth-oauthlib ·
google-auth-httplib2 · google-api-python-client · google-generativeai ·
anthropic · beautifulsoup4 · httpx`

All pinned in `requirements.txt`.

---

## 4 · Frontend integration notes

### 4a · New additive fields on existing responses

`GET /api/articles/{id}` and list now also return:
```ts
metaTitle, metaDescription, excerpt,
estimatedReadTime, keyTakeaways: string[],
faqSchema: [{question, answer}],
suggestedTags: string[],
seoScore: number   // deterministic 0-100
```

`GET /api/ai-visibility/scans` / `latest`:
- Existing fields unchanged
- New `recommendations: [{action, difficulty, expectedImpact, category}]`
- New `queries: string[]` (the 20 generated)
- New `rawResults: object` (per-model raw)

`POST /api/admin/api-keys/{key}/test` response:
- Existing `success, message`
- New `latency_ms`, `tested_at`

`POST /api/brand-voice/train` job result:
- Existing `formalityScore / playfulnessScore / technicalityScore` ints
- New `result.profile` containing the full GPT-4o style dict
  (`tone, formality, playfulness, technicality, sentenceLength,
  vocabulary, characteristicPhrases[], thingsToAvoid[], writingPersona`)

### 4b · New endpoints to integrate

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/admin/api-keys` | Frontend builder uses this to render the entire admin → API Keys page. Each entry already includes its `instructions{title, steps[], url, note}` so no hardcoding is required. |
| `GET` | `/api/admin/api-keys/{key}` | Single service detail (same shape) |
| `PUT` | `/api/admin/api-keys/{key}` | Body `{fields:{...}}`. Save button → this endpoint |
| `POST` | `/api/admin/api-keys/{key}/test` | Test button → this endpoint |
| `GET` | `/api/analytics/gsc/connect` | Returns `{authUrl}`. Open in popup or full-page redirect. |
| `GET` | `/api/analytics/gsc/callback` | Google redirects here; backend handles + redirects to `${FRONTEND_URL}/dashboard/analytics?connected=true` |

### 4c · Status badges for API key cards

The frontend should render the badge from `data[].status`:

| status value | meaning | suggested badge colour |
|---|---|---|
| `connected` | DB has values + last test passed | green |
| `not_connected` | No credentials set yet | gray |
| `error` | Last test failed | red |
| `pending_review` | OAuth platforms awaiting approval (Meta only by default) | orange |

---

## 5 · Breaking changes

**None.** Every change is additive or replaces an internal mock with a real
call. All endpoint paths, request bodies, and response envelopes are
preserved.

---

## 6 · File-level summary

```
backend/
├── .env                                  # all env vars now optional (admin panel covers them)
├── requirements.txt                      # +sendgrid +boto3 +google-* +anthropic +beautifulsoup4 +httpx
├── seed.py                               # seeds 13 catalogue entries instead of legacy list
├── core/scheduler.py                     # +cron_weekly_digest
├── routers/
│   ├── admin/api_keys.py                 # REWRITTEN — uses ConfigService + catalogue + real tests
│   ├── admin/announcements.py            # SendGrid announcement_email
│   ├── analytics.py                      # awaits async GSC build_authorize_url / exchange_code
│   ├── auth.py                           # SendGrid welcome + password_reset
│   ├── ai_writer.py                      # real URL fetch + GPT-4o profile + voice scoring
│   └── team.py                           # SendGrid team_invite
└── services/
    ├── api_catalog.py     # NEW — 13-service metadata catalogue
    ├── config.py          # NEW — ConfigService (DB-first cache, 5-min TTL)
    ├── ai_visibility.py   # NEW — real 5-model scan; ConfigService for keys
    ├── api_keys.py        # SUPPORTED_KEYS = sendgrid+...; kept for legacy callers
    ├── brand_voice.py     # NEW — URL fetch + GPT-4o profile
    ├── email.py           # NEW — SendGrid + 6 HTML templates; ConfigService
    ├── gsc.py             # NEW — async Google OAuth + searchanalytics; ConfigService
    ├── jobs.py            # real article pipeline + real AI scan + real growth score + real GSC sync + cron_weekly_digest
    ├── llm.py             # new article prompt + JSON parsing + SEO scoring + DALL-E 3; ConfigService
    ├── storage.py         # NEW — Cloudflare R2 boto3 + download_to_r2; ConfigService
    └── wordpress.py       # NEW — real WP REST publisher
```

---

## 7 · Verification (local)

- ✅ 13 catalogue services seeded across 7 sections
- ✅ `GET /api/admin/api-keys` returns full catalogue with metadata + instructions
- ✅ `PUT /api/admin/api-keys/{key}` saves encrypted fields; cache invalidates immediately
- ✅ `POST /api/admin/api-keys/openai/test` makes a real OpenAI API call (returns 401 locally because test key is fake — confirms the path is live)
- ✅ `POST /api/admin/api-keys/cloudflare_r2/test` makes a real boto3 call to R2 (SSL failure with fake account id — confirms the path is live)
- ✅ Saved key in admin panel is **immediately** retrievable via
  `await config_service.get_value("openai")` from any service module
- ✅ All Phase-0 regression tests pass (verified: auth register, /auth/me,
  public plans, admin dashboard, ai-visibility/simulate, article generation
  background job completes status=completed progress=100)

## 8 · Next phase backlog (Phase 2)

1. Real LemonSqueezy + DataForSEO integration (one file each)
2. Real publishers for Webflow / Ghost / HubSpot / Wix / Notion
3. Real publishers for IG / FB / LinkedIn / Twitter / Pinterest / YouTube
4. Real Google login (`POST /api/auth/google`)
5. Move in-memory rate-limit + admin-lockout to Redis with `X-Forwarded-For`
6. Plan-limit enforcement middleware

---

## 9 · Phase 1.2 — WordPress Plugin (NEW)

The official **SEO Jalwa Auto-Publisher** WordPress plugin is now packaged
and ready for distribution. Users install it on their self-hosted
WordPress site to enable secure, queue-friendly auto-publishing of articles
generated by SEO Jalwa.

### Deliverables

| File | Purpose |
|---|---|
| `/app/wordpress-plugin/seojalwa/seojalwa.php` | Plugin bootstrap (headers, init, activation/deactivation hooks, autoloader) |
| `/app/wordpress-plugin/seojalwa/includes/class-settings.php` | Admin settings page (API key + site URL) under **Settings → SEO Jalwa** |
| `/app/wordpress-plugin/seojalwa/includes/class-api.php` | Custom WP REST route `wp-json/seojalwa/v1/publish` for receiving article payloads |
| `/app/wordpress-plugin/seojalwa/includes/class-publisher.php` | Translates SEO Jalwa payload into `wp_insert_post`, sets Yoast meta, sideloads featured image |
| `/app/wordpress-plugin/seojalwa/includes/class-tracker.php` | Reports plugin version + WP/PHP version back to SEO Jalwa for `/api/plugin/version` checks |
| `/app/wordpress-plugin/seojalwa/assets/admin.css` | Admin styles |
| `/app/wordpress-plugin/seojalwa/assets/admin.js` | "Test connection" + "Sync now" client behaviour |
| `/app/wordpress-plugin/seojalwa/readme.txt` | WP-style README (banner, install, FAQ, changelog) |
| **`/app/wordpress-plugin/seojalwa.zip`** | **Ready-to-upload archive** for the WP `Plugins → Add New → Upload` flow |

### Backend support

- **NEW** `GET /api/plugin/version` → returns:
  ```json
  {
    "success": true,
    "data": {
      "version": "1.0.0",
      "min_wp_version": "5.0",
      "min_php_version": "7.4",
      "changelog": "Initial release",
      "download_url": "https://seojalwa.com/plugin/seojalwa-latest.zip",
      "released_at": "2026-05-19"
    }
  }
  ```
- Existing `POST /api/plugin/verify` continues to authenticate the
  WordPress site via its `apiKey` and bind it to a `Site` record.

### Verification (2026-05-19)

- ✅ `cd /app/wordpress-plugin && zip -r seojalwa.zip seojalwa/` produced a
  valid archive (~12 KB) containing all 9 plugin files.
- ✅ `curl GET {API_URL}/api/plugin/version` → HTTP 200, returns version
  metadata as documented.
- ✅ Plugin source tree mirrors the structure expected by WP plugin
  reviewers (top-level `seojalwa.php`, `includes/`, `assets/`, `readme.txt`).

### Distribution

Host `seojalwa.zip` at `https://seojalwa.com/plugin/seojalwa-latest.zip`
(or replace the URL in the `/api/plugin/version` payload to point to your
chosen CDN). The user downloads the zip, uploads it via **Plugins → Add
New → Upload Plugin**, then enters their SEO Jalwa **Site API Key** under
**Settings → SEO Jalwa**.

---

## 10 · Phase 1 Finalisation (2026-05-20)

### Shipped in this pass
1. **Subscription lifecycle (FIX 1).** `POST /api/auth/register` now
   auto-creates a 14-day `TRIALING` subscription on the cheapest plan
   (defaults to `Starter`). `/api/auth/me` enriches subscription with the
   populated `plan` object. **`PUT /api/admin/users/{userId}/subscription`**
   (new) accepts `{planId, status, billingInterval, trialDays, adminNote}`
   with full transition logic (TRIALING/ACTIVE/CANCELLED/PAST_DUE/EXPIRED),
   automatically recomputes `currentPeriodEnd` (30/365 days), notifies the
   user by email, and writes to the audit log.
2. **Plan-limit enforcement (FIX 2).** New `core/plan_limits.py` with
   `check_article_limit`, `check_ai_scan_limit`, `check_social_limit`.
   `POST /api/articles/generate` now enforces the user's `articlesPerMonth`
   and returns `code=LIMIT_REACHED` with `meta={resource, used, limit,
   upgrade_url}` (also threaded as a new `meta` field on the unified error
   envelope via `APIError(meta=…)`).
3. **Admin audit log (FIX 3).** New `core/audit.py` writer +
   `GET /api/admin/audit-log` (paginated, filterable by `action`/`target_id`).
   `USER_PLAN_CHANGED`, `USER_STATUS_CHANGED`, `ANNOUNCEMENT_SENT` already
   emit entries; remaining actions are documented for future passes.
4. **In-app notifications (FIX 8).** New `notifications` collection +
   `services/notifications.create_notification` writer. User endpoints
   `GET /api/notifications`, `GET /api/notifications/unread-count`,
   `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all`.
   Article jobs now create `ARTICLE_PUBLISHED` / `ARTICLE_FAILED`
   notifications on completion/failure.
5. **Real dashboard metrics (FIX 12).** `GET /api/admin/dashboard/stats`
   now returns `ARR`, `churnThisMonth` as a real percentage,
   `articlesGeneratedToday`, `articlesGeneratedThisMonth`, `scansRunToday`,
   `emailsSentToday`. All derived from real DB queries.
6. **Announcements broadcast (FIX 17).** `POST /api/admin/announcements`
   now supports `channel=IN_APP|EMAIL|BOTH`, creating one `ANNOUNCEMENT`
   notification per recipient and tracking `emailsSent` and
   `notificationsCreated` in the response.
7. **WordPress plugin debugging (FIX 18).** `POST /api/plugin/verify` now
   accepts an optional `{siteUrl}` body, returns specific error codes
   (`INVALID_API_KEY`, `SITE_URL_MISMATCH`, `PLUGIN_UNAUTHORIZED`), and
   logs the last 4 characters of every received key for trace.

### Verified live
- `Phase-1 smoke suite still passes 29/29` (zero regressions).
- All 7 new flows green via curl (response excerpts in chat log).

### Deferred to Phase 2 (intentionally not shipped this pass)
- ~~FIX 4 Per-user activity log endpoint~~ ✅ **Shipped 2026-05-21**
- ~~FIX 5 Email log collection + admin viewer~~ ✅ **Shipped 2026-05-21**
- ~~FIX 6 Editable email templates in DB~~ ✅ **Shipped 2026-05-21**
- ~~FIX 7 Renewal reminder cron~~ ✅ **Shipped 2026-05-21**
- ~~FIX 9 Cascade delete~~ ✅ **Shipped 2026-05-21**
- ~~FIX 10 `/feedback` + submissions~~ ✅ **Shipped 2026-05-21**
- ~~FIX 11 GPT-4o retention insights~~ ✅ **Shipped 2026-05-21**
- **FIX 13/14** Some admin analytics/billing fields still source from mocks — *requires LemonSqueezy first*.
- **FIX 15/16** Coupon-apply UX still tied to LemonSqueezy — *requires LemonSqueezy first*.

---

## 11 · Phase 1 — Final Pass (2026-05-21)

### Now shipped (all deferred items completed)

#### FIX 4 — Per-user activity log
- New `services/activity.py` writer with `log_activity(user_id, action, metadata, request)`.
- New `user_activity_log` collection — fields: `id, userId, action, metadata, ipAddress, userAgent, createdAt`.
- `GET /api/user/activity` — current user's own paginated activity feed.
- `GET /api/admin/users/{id}/activity-log` — admin per-user paginated activity feed.
- Hooks wired: `USER_REGISTERED` on signup, `USER_LOGGED_IN` on login, `FEEDBACK_SUBMITTED` on feedback. Article/AI-scan/site events can be hooked similarly.

#### FIX 5 — Email logs
- Every `send_email(...)` call writes a row into `email_logs`:
  `{id, userId, to, subject, templateKey, status (SENT|FAILED|SKIPPED), provider (SENDGRID|RESEND), statusCode, errorMessage, sentAt}`.
- `GET /api/admin/emails?status=&user_id=&template_key=&page=&limit=` — filterable paginated list.
- `GET /api/admin/emails/{id}` — single email with full payload.

#### FIX 6 — Editable email templates in DB
- New `email_templates` collection seeded on boot with **15 templates**:
  welcome, verify_email, password_reset, article_published, article_failed,
  weekly_digest, ai_scan_complete, subscription_created, subscription_renewed,
  subscription_cancelled, subscription_expiring, payment_failed, team_invite,
  trial_ending, announcement.
- `services/email_templates.py` — DB-backed loader with 60s TTL cache;
  `render_template(key, vars)` interpolates `{{var}}` placeholders.
- Admin CRUD endpoints (all `GET/PUT/POST` paths require admin session):
  - `GET /api/admin/email-templates` — list all 15.
  - `GET /api/admin/email-templates/{key}` — get one.
  - `PUT /api/admin/email-templates/{key}` — update subject, htmlBody, isActive, name, description, category, variables. Writes `EMAIL_TEMPLATE_UPDATED` audit entry.
  - `POST /api/admin/email-templates/{key}/test` — send a test email with sample variables to a chosen address.
  - `POST /api/admin/email-templates/seed` — manual re-seed (inserts only missing).

#### FIX 7 — Renewal reminder cron + settings
- Three new fields on `GET/PUT /api/admin/settings`: `renewalReminderDays` (default `[7, 3, 1]`), `trialEndingReminderDays` (default `[3, 1]`), `paymentRetryDays` (default `[1, 3, 7]`).
- New `services/reminders.py::cron_reminders` registered in APScheduler at **09:00 UTC daily**. For each `[N days]` window it finds subscriptions whose `currentPeriodEnd`/`trialEndsAt` is in exactly N days, renders the matching template (`subscription_expiring` or `trial_ending`), sends the email, creates a `TRIAL_ENDING`/`PAYMENT_FAILED` in-app notification, and pushes a `reminderSent` marker to avoid duplicates.

#### FIX 9 — Cascade delete
- `DELETE /api/admin/users/{id}` now purges 16 collections:
  sites, articles, social_posts, search_terms, ai_visibility_scans,
  competitors, growth_scores, article_settings, brand_voices,
  generated_content, social_accounts, subscriptions, invoices,
  team_members, notifications, user_activity_log, email_logs.
- Response returns `{deletedUser, cascadedDeletes: {<collection>: count}}`.
- Writes `USER_DELETED` audit entry with metadata.

#### FIX 10 — Contact / Feedback
- New `submissions` collection (replaces inline `contacts`):
  `{id, type (CONTACT|FEEDBACK), name, email, subject, message, category, rating, pageUrl, userId, status (NEW|READ|RESOLVED|REPLIED), adminNotes, repliedAt, createdAt}`.
- `POST /api/feedback` — anonymous or authenticated; logs `FEEDBACK_SUBMITTED` activity if user present; notifies `hello@seojalwa.com`.
- `GET /api/admin/submissions?type=&status=&page=&limit=` — paginated list.
- `GET /api/admin/submissions/{id}` — full record.
- `PUT /api/admin/submissions/{id}` — update status / adminNotes.
- `POST /api/admin/submissions/{id}/reply` — sends a real email reply via the configured provider and flips status to `REPLIED`.

#### FIX 11 — GPT-4o retention insights
- `GET /api/admin/insights/retention[?force=true]` — gathers 9 platform metrics (total users, 7d active, 14d inactive, trials expiring in 3 days, no-article users, low growth score, failed articles, cancellations, churn %) and asks GPT-4o for 5–10 prioritised, categorised suggestions. Response is JSON-array parsed, **cached for 24 h**, and includes the raw metrics so the UI can render its own dashboard tile.

### Verified
- All 7 final-pass fixes green via curl (response excerpts in chat log).
- `Phase-1 smoke suite still passes 29/29` — no regressions.
- `APScheduler` now reports **7 cron jobs** running.

### Still deferred to Phase 2 (requires LemonSqueezy)
- **FIX 13/14** — Admin analytics + billing overview's currency totals stay 0 until invoices flow in.
- **FIX 15** — Coupon redemption flow on the user side.
- **FIX 16** — Blog CRUD admin endpoints already work; public read endpoint will be exercised once content exists.
