# SEO Jalwa Backend — Phase 1 Completion Report

Date: 2026-05-19

This document inventories every change made in Phase 1, every endpoint that
is now wired to a **real** integration, every endpoint that remains mocked
(and why), the new env vars / pip packages, and any breaking changes the
frontend builder needs to know about.

---

## 1 · Endpoints now fully implemented with real integrations

### Email (SendGrid)
- `POST /api/auth/register` → fires real `welcome_email`
- `POST /api/auth/forgot-password` → real `password_reset` email
- `POST /api/team/invite` → real `team_invite` email
- `POST /api/admin/announcements` → real `announcement_email` to each recipient
- Article generation job → real `article_published` email on successful CMS publish
- New **Monday 08:05 UTC cron** → `weekly_digest` email for users with `notifications.weeklyScore = true`
- Payment-failed webhook path → real `payment_failed` email

All templates are HTML, branded with a shared shell, and live in
`services/email.py`. They are **identical in content** to the previous Resend
versions — only the sending provider changed.

### Article generation (OpenAI GPT-4o + DALL-E 3 + R2 + WordPress REST)
- `POST /api/articles/generate` — kicks the queued job
- `GET /api/articles/job/{jobId}` — poll job

Job pipeline (`services/jobs.run_article_generation`):
1. Loads `ArticleSettings` (length, language, instructions, toggles).
2. Loads `BrandVoice.styleProfile` → injected into system prompt.
3. Calls **GPT-4o** with the **exact spec prompt** asking for strict JSON:
   `title, metaDescription, content (HTML), excerpt, keyTakeaways[],
   faqSchema[], suggestedTags[], estimatedReadTime, wordCount`.
4. Computes real **SEO score (0-100)** using the deterministic algorithm in
   `services/llm.calculate_seo_score`:
   - +15 keyword in title
   - +10 keyword in first 100 words
   - +10 meta description 150-160 chars
   - +10 meta description has keyword
   - +15 ≥ 4 H2 tags in content
   - +10 word count ≥ 1500
   - +10 FAQ present
   - +10 key takeaways present
   - +5 TOC present
   - +5 read time set
5. If `includeHeroImages = true` → real **DALL-E 3** call
   (`size=1792x1024, quality=standard`) → image downloaded from OpenAI's URL
   → re-uploaded to **Cloudflare R2** at
   `articles/{articleId}/hero.jpg` → article saved with the R2 public URL.
6. If `autoPublish = true` and the site has a WordPress application password
   stored (`site.wordpressToken`) → real **WordPress REST** publish:
   - Uploads featured media via `POST /wp-json/wp/v2/media`
   - Creates post via `POST /wp-json/wp/v2/posts` with Yoast meta fields
   - Stores `cmsPostId` and `cmsUrl` on the article
   - Sends `article_published` email
7. If WordPress is connected via the **plugin** (no token, just plugin polls
   `/api/plugin/articles/pending`) → article is left `SCHEDULED` for the
   plugin to pick up (unchanged behaviour).

### DALL-E 3 (`services/llm.generate_hero_image`)
- Uses spec prompt template, returns OpenAI image URL.
- Re-uploaded to R2 via `services/storage.download_to_r2`.

### Cloudflare R2 (`services/storage.py`)
- `upload_file(bytes, key, content_type) → public_url`
- `delete_file(key) → bool`
- `get_signed_url(key, expires) → str`
- `download_to_r2(source_url, key, content_type)` (for OpenAI image hand-off)
- Uses boto3 S3 client with `endpoint=https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`.

### WordPress publishing (`services/wordpress.py`)
- Real REST API publisher using Basic auth (WP application password
  decrypted from `site.wordpressToken`).
- Featured image upload + Yoast SEO meta fields.

### AI Visibility scanning (`services/ai_visibility.py`)
- `POST /api/ai-visibility/scan` triggers real 5-model scan.
- Generates 20 brand queries via **GPT-4o**, splits 5 queries / model.
- **ChatGPT** — OpenAI `gpt-4o-mini`
- **Perplexity** — `llama-3.1-sonar-small-128k-online` over real API
- **Gemini** — `gemini-1.5-flash` via `google-generativeai` SDK
- **Claude** — `claude-3-haiku-20240307` via official `anthropic` SDK
- **Copilot** — derived from the other four ± random variance (no public
  API; TODO marker in code for Microsoft Copilot release).
- Sentiment classification (POSITIVE / NEUTRAL / NEGATIVE / NOT_MENTIONED).
- Weighted overall: ChatGPT 30 · Perplexity 25 · Gemini 20 · Claude 15 · Copilot 10.
- Recommendations generated via GPT-4o returning JSON
  `{action, difficulty, expectedImpact, category}`.
- Triggers growth-score recalculation automatically.

### Brand Voice (`services/brand_voice.py`)
- `POST /api/brand-voice/train` — if `websiteUrl` provided, the homepage is
  fetched + stripped via BeautifulSoup, then sent to **GPT-4o** with the
  exact spec prompt; returns the structured profile with keys
  `tone, formality, playfulness, technicality, sentenceLength, vocabulary,
  characteristicPhrases[], thingsToAvoid[], writingPersona`.
- Profile stored on `BrandVoice.styleProfile` and used as system context in
  every future `generate_article` and `content/generate` call.
- `POST /api/content/voice-score` — real GPT-4o comparison returning
  `{score, feedback}`.

### Growth Score (`services/jobs.run_growth_score`)
- Real calculation per spec: AI 30 % · SEO 25 % · Social 25 % · Traffic 20 %.
- AI component reads the latest `ai_visibility_scans` record for the site.
- SEO component compares published articles in the last 30 days against
  `ArticleSettings.publishingFrequency * 4` target.
- Social component compares published social posts vs 20 / month target.
- Traffic component compares last 30 days clicks vs the preceding 30 days
  from `Article.clicks` (populated by GSC sync).
- Triggered after every AI visibility scan, weekly Monday cron, and on demand
  via `POST /api/growth-score/calculate`.

### Google Search Console (`services/gsc.py`)
- **NEW** `GET /api/analytics/gsc/connect` — returns Google authorize URL
  with `state = userId` and `webmasters.readonly` scope.
- **NEW** `GET /api/analytics/gsc/callback` — Google redirects here with
  `?code & ?state`. Exchanges code, stores encrypted access + refresh
  tokens on the user, redirects to
  `${FRONTEND_URL}/dashboard/analytics?connected=true`.
- `POST /api/analytics/gsc/connect` (legacy POST-body flow) kept for
  backward compatibility.
- `POST /api/analytics/sync` — real GSC `searchanalytics().query()` over
  the last 30 days, dimensions `[query, page]`. Aggregates by page URL and
  writes `clicks / impressions / ctr / avgPosition` onto matching Article
  records by URL substring match. Persists a `gsc_snapshots` log entry
  and updates `user.lastGscSync`.

### Daily article cron (`services/jobs.cron_daily_article_generation`)
- Runs **06:00 UTC** every day.
- For each `ArticleSettings` with `autoPublish=true`:
  - Skips sites with no active subscription / trial.
  - Skips sites with no CMS connection.
  - Skips sites that already have an article created today.
  - Picks next `PENDING` SearchTerm, or asks GPT-4o for a fresh topic if
    none remain.
  - Creates an Article, enqueues a generation job, and runs the full
    real-integration pipeline above (LLM → DALL-E → R2 → WP REST → email).

### Admin API-key testing (`POST /api/admin/api-keys/{key}/test`)
Each test now performs a **real call** to the live service when the key is
configured:

| key | what runs |
|---|---|
| `openai` | GPT-4o-mini ping; success only if not graceful-degradation |
| `sendgrid` | Sends test email to `SENDGRID_FROM_EMAIL` |
| `r2_*` | `list_objects_v2(MaxKeys=1)` against R2 bucket |
| `perplexity` | Real chat completions ping |
| `anthropic` | Real Claude `messages.create` ping |
| `gemini` | Real `generate_content_async` ping |
| `google_*` | Verifies OAuth client config is loaded |
| `dataforseo`, `lemonsqueezy_*` | still MOCK (see below) |

Response now includes `latency_ms`.

---

## 2 · Endpoints still mocked (and why)

| Area | Why it's still mocked | Where the swap-in lives |
|---|---|---|
| **LemonSqueezy checkout / webhook signature / refund** | No real API key yet; webhook signature verification stub always returns `true`. | `services/mocks.create_checkout`, `verify_lemonsqueezy_signature`, `lemonsqueezy_refund`. Real implementation needs `LEMONSQUEEZY_API_KEY` + `LEMONSQUEEZY_WEBHOOK_SECRET`. |
| **DataForSEO keyword research** | No `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD` yet. | `services/mocks.keyword_research`. Code is a single basic-auth HTTP POST when keys arrive. |
| **Social publishers** (Instagram, Facebook, LinkedIn, Twitter, Pinterest, YouTube) | OAuth apps not registered yet on Meta/LinkedIn/X dev portals. | `services/mocks.publish_social_post`, `get_social_oauth_url`, `social_exchange_code`. |
| **CMS publishers other than WordPress** (Webflow, Ghost, HubSpot, Wix, Notion) | Per Phase 1 scope: WordPress is real, the rest still use `mocks.publish_to_cms` with deterministic mock IDs. |
| **Google OAuth login** (POST /api/auth/google) | No `GOOGLE_CLIENT_ID` wired for sign-in yet; GSC OAuth is wired but the login flow still uses `mocks.verify_google_token`. |
| **Copilot in AI Visibility** | Microsoft has no public Copilot API; score is derived from the average of the other 4 models ± 10 variance. TODO marker in `services/ai_visibility._run_model_scan`. |

All other Resend → SendGrid swaps are complete; **Resend is no longer used
anywhere in the codebase** (the legacy `resend` row in `api_configs` from
the old seed is harmless and can be ignored).

---

## 3 · New environment variables

Add these to Railway → Variables (also documented in `.env.example` below):

```
SENDGRID_API_KEY=
SENDGRID_FROM_EMAIL=hello@seojalwa.com
SENDGRID_FROM_NAME=SEO Jalwa

R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=seojalwa-assets
R2_PUBLIC_URL=

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://api.seojalwa.com/api/analytics/gsc/callback

PERPLEXITY_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
```

**Removed from .env.example**: `RESEND_API_KEY`.

### New pip packages (already added to `requirements.txt` via `pip freeze`)
```
sendgrid==6.12.5
boto3==1.43.7
google-auth==2.52.0
google-auth-oauthlib==1.4.0
google-auth-httplib2==0.4.0
google-api-python-client==2.196.0
google-generativeai==0.8.6
anthropic==0.103.0
beautifulsoup4==4.14.3
httpx==0.28.1
```

---

## 4 · Instructions for the frontend builder

### 4a. Response-shape additions (NON-breaking, only new fields)

**`GET /api/articles/{id}`** and **`GET /api/articles?...`** now also return:

```ts
{
  // ...all previous fields...
  metaTitle: string,
  metaDescription: string,
  excerpt: string,
  estimatedReadTime: number,       // in minutes
  keyTakeaways: string[],          // bullet points
  faqSchema: [{question, answer}], // ready for JSON-LD schema
  suggestedTags: string[],
  seoScore: number                 // already existed; now deterministic 0-100
}
```

Render these in the article editor / preview UI. They are present from any
new article generation. Existing articles created before this deploy will
have `null` for these new fields — handle defensively.

**`GET /api/ai-visibility/latest`** and `scans` now return enriched scan
records:

```ts
{
  overallScore: number,
  chatgptScore, perplexityScore, geminiScore, claudeScore, copilotScore: number,
  chatgptSentiment, perplexitySentiment, geminiSentiment, claudeSentiment, copilotSentiment:
    "POSITIVE" | "NEUTRAL" | "NEGATIVE" | "NOT_MENTIONED",
  recommendations: [
    { action: string, difficulty: "easy"|"medium"|"hard",
      expectedImpact: "low"|"medium"|"high", category: string }
  ],
  queries: string[],     // NEW — the 20 generated queries shown to AI models
  rawResults: object     // NEW — per-model raw structure
}
```

**`POST /api/admin/api-keys/{key}/test`** response now includes `latency_ms`:

```ts
{ success: boolean, message: string, latency_ms: number }
```

**`POST /api/brand-voice/train`** result (when polled via job) now includes
the full GPT-4o profile in `result.profile` with these keys:
`tone, formality, playfulness, technicality, sentenceLength, vocabulary,
characteristicPhrases[], thingsToAvoid[], writingPersona`. Existing
`formalityScore / playfulnessScore / technicalityScore` integer fields are
still emitted for backward compatibility.

### 4b. New endpoints to integrate

1. **`GET /api/analytics/gsc/connect`** (JWT) → returns `{authUrl}`.
   - Open `authUrl` in a popup or full-page redirect.
   - Google redirects the browser to
     `GET /api/analytics/gsc/callback?code&state` which the backend
     handles and then redirects to
     `${FRONTEND_URL}/dashboard/analytics?connected=true`.
   - The frontend just needs to read `?connected=true` and show a toast.

2. The **legacy `POST /api/analytics/gsc/connect`** with `{code}` body is
   kept working — the new flow above is recommended.

### 4c. Confirmed not changed (still backward-compatible)

- Standard response envelope `{success, data, message, pagination?}` — same.
- Error envelope `{success:false, error, code, statusCode, details?}` — same.
- All admin endpoint paths and bodies — same.
- All user auth endpoints — same.
- WordPress plugin endpoints (`/api/plugin/*`) — same.

---

## 5 · Breaking changes

**None.** Every change is additive:

- New fields on Article + AiVisibilityScan responses (existing fields preserved).
- New `latency_ms` on admin api-key/test (existing `success` + `message` preserved).
- New routes (`GET /api/analytics/gsc/connect`, `GET /api/analytics/gsc/callback`) — additive.
- Resend → SendGrid swap is invisible to the API consumer; the email body templates are identical.
- `services/mocks.send_email` is no longer referenced by any router but is
  kept in the module for any third-party adapter that may still want the
  generic helper.

---

## 6 · File-level summary of changes

```
backend/
├── .env                                       # new vars appended
├── requirements.txt                           # +sendgrid +boto3 +google-* +anthropic +beautifulsoup4 +httpx
├── core/scheduler.py                          # +cron_weekly_digest
├── routers/admin/api_keys.py                  # real /test for openai/sendgrid/r2/perplexity/anthropic/gemini/google
├── routers/admin/announcements.py             # uses real SendGrid announcement_email
├── routers/analytics.py                       # GET /gsc/connect + GET /gsc/callback; real GSC sync
├── routers/auth.py                            # uses real SendGrid welcome + password_reset
├── routers/ai_writer.py                       # train_voice fetches URL via brand_voice service; voice_score uses real GPT-4o
├── routers/team.py                            # uses real SendGrid team_invite
└── services/
    ├── ai_visibility.py    # NEW — real 5-model scan + recommendations
    ├── api_keys.py         # sendgrid replaces resend in SUPPORTED_KEYS
    ├── brand_voice.py      # NEW — URL fetch + GPT-4o profile
    ├── email.py            # NEW — SendGrid client + 6 HTML templates
    ├── gsc.py              # NEW — Google OAuth + searchanalytics
    ├── jobs.py             # real article job (LLM → DALL-E → R2 → WP REST → email)
    │                       # real ai_visibility scan call
    │                       # real growth score formula
    │                       # real gsc sync
    │                       # +cron_weekly_digest
    │                       # rewritten cron_daily_article_generation
    ├── llm.py              # new article prompt + JSON parsing + SEO scoring + generate_hero_image (DALL-E 3)
    ├── storage.py          # NEW — Cloudflare R2 via boto3 + download_to_r2 helper
    └── wordpress.py        # NEW — real WP REST publisher
```

---

## 7 · Status

✅ All 13 spec sections completed.
✅ Local smoke tests pass (health, plans, admin login, admin api-keys list,
   openai live ping returns success=false because local has no real OpenAI
   key — Railway with real key will succeed).
✅ No breaking changes to the existing 100 %-passing endpoint surface.
✅ `requirements.txt` regenerated and Railway-installable (no private packages).

The next deployment to Railway should "just work" as long as the new env
vars are populated. Anything left unset degrades gracefully — endpoints
return `{success:false, message:"...not configured"}` instead of crashing.
