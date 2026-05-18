# SEO Jalwa — API Endpoints Reference

Base URL: `https://growth-engine-api.preview.emergentagent.com` (or `process.env.REACT_APP_BACKEND_URL`).
Every endpoint below is mounted under `/api/*` unless noted.

Swagger UI: **`/api/docs`** &nbsp;·&nbsp; OpenAPI JSON: **`/api/openapi.json`**

Auth legend:
- 🔓 **None** — public, no auth header
- 🟦 **JWT** — `Authorization: Bearer <accessToken>`
- 🟥 **Admin** — `X-Admin-Token: <token>` _or_ cookie `admin_session=<token>`
- 🔑 **Plugin** — `X-Jalwa-API-Key: <site.apiKey>`

Every success response is `{ success: true, data, message, pagination? }`.
Every error response is `{ success: false, error, code, statusCode, details? }`.

---

## 0 · System
| Method | Path | Auth | What it does | Response data |
| --- | --- | --- | --- | --- |
| GET | `/health` | 🔓 (local only — bypasses ingress) | Health check | `{ status, timestamp, database }` |
| GET | `/api/health` | 🔓 | Health check (public) | `{ status, timestamp, database }` |
| GET | `/api/` | 🔓 | API banner | `{ name, version, docs }` |
| GET | `/api/docs` | 🔓 | Swagger UI | HTML |
| GET | `/api/openapi.json` | 🔓 | OpenAPI 3 schema | JSON schema |

---

## 1 · Auth — `/api/auth`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| POST | `/auth/register` | 🔓 | Register, create default site, queue verify-email | `{ fullName, email, password, websiteUrl? }` | `{ user, accessToken, refreshToken }` |
| POST | `/auth/login` | 🔓 | Email + password login | `{ email, password }` | `{ user, accessToken, refreshToken }` |
| POST | `/auth/google` | 🔓 | Google OAuth login (mock verifier) | `{ googleToken }` | `{ user, accessToken, refreshToken }` |
| POST | `/auth/refresh` | 🔓 | Exchange refresh for new access | `{ refreshToken }` | `{ accessToken }` |
| POST | `/auth/logout` | 🟦 | Logout (stateless ack) | – | `{ loggedOut }` |
| POST | `/auth/verify-email/{token}` | 🔓 | Mark email verified | – | `{ verified }` |
| POST | `/auth/forgot-password` | 🔓 | Send reset-password email | `{ email }` | `{ sent }` |
| POST | `/auth/reset-password` | 🔓 | Apply new password using token | `{ token, newPassword }` | `{ reset }` |
| GET | `/auth/me` | 🟦 | Current user + active subscription + sites | – | `{ user, subscription, sites }` |

---

## 2 · Sites — `/api/sites`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/sites` | 🟦 | List user's sites | – | `[ site, ... ]` |
| POST | `/sites` | 🟦 | Create site (auto-generates `apiKey`) | `{ name, url, platform }` | `site` |
| GET | `/sites/{id}` | 🟦 | Get site detail | – | `site` |
| PUT | `/sites/{id}` | 🟦 | Update site fields | `{ name?, url?, platform? }` | `site` |
| DELETE | `/sites/{id}` | 🟦 | Soft-delete site | – | `{ deleted }` |
| POST | `/sites/{id}/verify-connection` | 🟦 | Is plugin connected? | – | `{ connected, lastSync }` |
| POST | `/sites/{id}/connect/ghost` | 🟦 | Store Ghost API key (encrypted) | `{ apiKey, siteUrl }` | `{ connected }` |
| POST | `/sites/{id}/connect/webflow` | 🟦 | Exchange Webflow OAuth code (mock) | `{ code }` | `{ connected }` |
| POST | `/sites/{id}/connect/hubspot` | 🟦 | Exchange HubSpot OAuth code (mock) | `{ code }` | `{ connected }` |
| POST | `/sites/{id}/connect/wix` | 🟦 | Store Wix API key (encrypted) | `{ apiKey, siteId }` | `{ connected }` |
| POST | `/sites/{id}/connect/notion` | 🟦 | Exchange Notion OAuth code (mock) | `{ code }` | `{ connected }` |

`platform` ∈ `WORDPRESS · SHOPIFY · WEBFLOW · GHOST · WIX · SQUARESPACE · NEXTJS · NOTION · HUBSPOT · OTHER`

---

## 3 · Social accounts & posts — `/api/social`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/social/accounts` | 🟦 | List connected social accounts | – | `[ account ]` (tokens stripped) |
| GET | `/social/auth/{platform}` | 🟦 | Get OAuth start URL | – | `{ authUrl, state }` |
| GET | `/social/callback/{platform}` | 🔓 (state) | OAuth callback, stores tokens, redirects | `?code&state` | redirect to `/dashboard/connections` |
| DELETE | `/social/accounts/{id}` | 🟦 | Disconnect | – | `{ disconnected }` |
| GET | `/social/posts` | 🟦 | List posts (paginated, filters: `siteId`, `platform`, `status`) | – | `[ post ]` |
| POST | `/social/posts` | 🟦 | Create posts on N platforms | `{ siteId, platforms[], caption, imageUrl?, scheduledAt?, hashtags[] }` | `{ postIds }` |
| POST | `/social/posts/generate` | 🟦 | Generate per-platform posts from an article (BG job) | `{ articleId, platforms[] }` | `{ jobId, status }` |
| PUT | `/social/posts/{id}` | 🟦 | Update post | `{ caption?, scheduledAt?, status?, imageUrl? }` | `{ updated }` |
| DELETE | `/social/posts/{id}` | 🟦 | Delete post | – | `{ deleted }` |
| POST | `/social/posts/{id}/approve` | 🟦 | Move PENDING_APPROVAL → SCHEDULED | – | `{ approved }` |
| POST | `/social/posts/{id}/publish-now` | 🟦 | Publish immediately | – | `{ published, platformPostId }` |
| GET | `/social/analytics` | 🟦 | Aggregate reach/likes/clicks | `?siteId&platform&dateRange` | `{ totalPosts, totalReach, totalLikes, totalClicks }` |

`platform` ∈ `INSTAGRAM · FACEBOOK · LINKEDIN · TWITTER · PINTEREST · YOUTUBE`

---

## 4 · Articles — `/api/articles`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/articles` | 🟦 | List articles (paginated, filters: `siteId`, `status`, `search`) | – | `[ article ]` |
| GET | `/articles/calendar` | 🟦 | Articles grouped by day | `?siteId&year&month` | `{ "YYYY-MM-DD": [ articles ] }` |
| POST | `/articles/generate` | 🟦 | Queue article-generation job | `{ siteId, searchTerm, settingsOverride? }` | `{ jobId, articleId, status }` |
| GET | `/articles/job/{jobId}` | 🟦 | Poll job status | – | `{ status, progress, result? }` |
| GET | `/articles/{id}` | 🟦 | Get full article | – | `article` |
| PUT | `/articles/{id}` | 🟦 | Edit article | `{ title?, content?, metaTitle?, metaDescription?, excerpt?, scheduledAt?, status? }` | `{ updated }` |
| POST | `/articles/{id}/publish` | 🟦 | Publish article to CMS + auto-generate social | `{ destination, siteId? }` | `{ success, url }` |
| DELETE | `/articles/{id}` | 🟦 | Soft delete | – | `{ deleted }` |
| POST | `/articles/{id}/reschedule` | 🟦 | Change scheduledAt | `{ scheduledAt }` | `{ rescheduled }` |

`destination` ∈ `wordpress · webflow · ghost · hubspot · wix · notion`

---

## 5 · Search terms — `/api/search-terms`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/search-terms` | 🟦 | List terms for site | `?siteId` | `[ term ]` |
| POST | `/search-terms` | 🟦 | Add many terms (with mocked DataForSEO research) | `{ siteId, terms[] }` | `{ created, terms }` |
| DELETE | `/search-terms/{id}` | 🟦 | Remove | – | `{ deleted }` |
| POST | `/search-terms/ai-suggest` | 🟦 | Real LLM topic ideation | `{ siteId }` | `{ suggested, terms }` |

---

## 6 · Article settings — `/api/article-settings`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/article-settings/{siteId}` | 🟦 | Get settings (defaults if none) | – | `settings` |
| PUT | `/article-settings/{siteId}` | 🟦 | Upsert settings | full settings object (all optional) | `settings` |

Settings fields: `autoPublish, delayPublishing, includeHeroImages, includeYoutubeVideos, includeInfographics, includeKeyTakeaways, includeTableOfContents, addExternalLinks, articleLength (WORDS_1000|2000|3000|5000), publishingFrequency (1-7), writingLanguage, writingInstructions, websiteTitle, websiteDescription, targetCountry, targetCity, whatYouSell, whatYouDontSell, imageryPrompt`.

---

## 7 · AI Visibility — `/api/ai-visibility`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/ai-visibility/scans` | 🟦 | Scan history | `?siteId&limit` | `[ scan ]` |
| GET | `/ai-visibility/latest` | 🟦 | Latest scan for site | `?siteId` | `scan \| null` |
| POST | `/ai-visibility/scan` | 🟦 | Queue scan job | `{ siteId }` | `{ jobId, status }` |
| GET | `/ai-visibility/scan/{jobId}` | 🟦 | Poll scan job | – | `{ status, progress, result? }` |
| GET | `/ai-visibility/competitors` | 🟦 | Competitor records for site | `?siteId` | `[ competitor ]` |
| POST | `/ai-visibility/simulate` | 🔓 (5/hr per IP) | Single-query against 5 AI models | `{ query }` | `{ query, results: { chatgpt, perplexity, gemini, claude, copilot } }` |

---

## 8 · AI Writer — brand voice + content
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/brand-voice/{siteId}` | 🟦 | Get brand voice profile | – | `brandVoice \| null` |
| POST | `/brand-voice/train` | 🟦 | Train voice from samples / URL (BG job) | `{ siteId, contentSamples[]? , websiteUrl? }` | `{ jobId, status }` |
| POST | `/content/generate` | 🟦 | Generate content (**real OpenAI**) | `{ siteId, type, topic, brief?, targetKeyword? }` | `generatedContent` |
| POST | `/content/voice-score` | 🟦 | Score text against brand voice | `{ siteId, content }` | `{ score, feedback }` |
| GET | `/content/library` | 🟦 | List generated content (paginated, `siteId`, `type`) | – | `[ generatedContent ]` |
| DELETE | `/content/{id}` | 🟦 | Delete content | – | `{ deleted }` |

`type` ∈ `BLOG_ARTICLE · EMAIL · AD_COPY · SOCIAL_CAPTION · PRODUCT_DESCRIPTION`

---

## 9 · Auto-publish — `/api/publish`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/publish/connections/{siteId}` | 🟦 | CMS connection map | – | `{ wordpress, webflow, ghost, hubspot, wix, notion }` |
| POST | `/publish/publish/{articleId}` | 🟦 | Publish article to a CMS | `{ platform, siteId }` | `{ success, url }` |

---

## 10 · Analytics — `/api/analytics`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/analytics/overview` | 🟦 | Aggregated KPIs | `?siteId&dateRange` | `{ totalClicks, totalImpressions, avgCTR, avgPosition, dateRange }` |
| GET | `/analytics/articles` | 🟦 | Per-article GSC data (paginated) | `?siteId&page&limit` | `[ article ]` |
| GET | `/analytics/search-terms` | 🟦 | Top terms | `?siteId&limit` | `[ term ]` |
| GET | `/analytics/top-pages` | 🟦 | Top performing pages | `?siteId&limit` | `[ article ]` |
| POST | `/analytics/sync` | 🟦 | Trigger GSC sync (mock) | `{ siteId }` | `{ synced, lastSync, totalClicks }` |
| POST | `/analytics/gsc/connect` | 🟦 | Exchange GSC OAuth code (mock) | `{ code }` | `{ connected }` |

---

## 11 · Growth Score — `/api/growth-score`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/growth-score` | 🟦 | Latest + history | `?siteId` | `{ latest, history[] }` |
| POST | `/growth-score/calculate` | 🟦 | Recompute now (weighted 30/25/25/20) | `{ siteId }` | `{ score, breakdown }` |

---

## 12 · Team — `/api/team`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/team` | 🟦 | List members | – | `[ member ]` |
| POST | `/team/invite` | 🟦 | Invite by email + assign sites | `{ email, role, siteIds[], canAccessBilling }` | `member` |
| PUT | `/team/{id}` | 🟦 | Update role / billing / sites | `{ role?, canAccessBilling?, siteIds? }` | `{ updated }` |
| DELETE | `/team/{id}` | 🟦 | Remove member | – | `{ removed }` |
| GET | `/team/accept/{token}` | 🔓 | Accept invite | – | `{ activated } \| { requiresSignup, email, token }` |

`role` ∈ `ADMIN · EDITOR · VIEWER`

---

## 13 · User settings — `/api/user`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| PUT | `/user/profile` | 🟦 | Update profile | `{ fullName?, email?, websiteUrl? }` | `{ updated }` |
| PUT | `/user/password` | 🟦 | Change password | `{ currentPassword, newPassword }` | `{ updated }` |
| PUT | `/user/notifications` | 🟦 | Notification toggles | `{ emailDigest?, weeklyScore?, aiAlerts?, billingAlerts? }` | `{ updated }` |
| DELETE | `/user/account` | 🟦 | Delete account + cancel subs | `{ password }` | `{ deleted }` |

---

## 14 · Billing — `/api/billing`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/billing/plans` | 🟦 | Active plans | – | `[ plan ]` |
| POST | `/billing/checkout` | 🟦 | Create LemonSqueezy checkout (mock) | `{ planId, interval }` | `{ checkoutUrl, checkoutId }` |
| GET | `/billing/subscription` | 🟦 | Current subscription | – | `subscription` |
| POST | `/billing/cancel` | 🟦 | Cancel at period end | – | `{ cancelAtPeriodEnd }` |
| POST | `/billing/reactivate` | 🟦 | Reactivate | – | `{ reactivated }` |
| GET | `/billing/invoices` | 🟦 | Invoice history | – | `[ invoice ]` |
| POST | `/billing/webhook` | 🔓 (HMAC) | LemonSqueezy webhook receiver | LemonSqueezy event payload | `{ received }` |
| POST | `/billing/apply-coupon` | 🔓 | Validate coupon | `{ code }` | `{ valid, discount?, type? }` |

`interval` ∈ `monthly · annual`

---

## 15 · WordPress plugin — `/api/plugin`
All require header `X-Jalwa-API-Key: <site.apiKey>`.

| Method | Path | What it does | Body | Data |
| --- | --- | --- | --- | --- |
| POST | `/plugin/verify` | Confirm key is valid + mark site connected | – | `{ valid, siteName, userId }` |
| POST | `/plugin/ping` | Keep-alive | – | `{ pong, lastSync }` |
| GET | `/plugin/articles/pending` | Articles SCHEDULED & due to publish | – | `[ article ]` |
| POST | `/plugin/articles/{id}/confirm` | Confirm WP published it | `{ wordpressPostId, wordpressUrl }` | `{ confirmed }` |
| POST | `/plugin/track` | Basic analytics event | `{ pageUrl, event }` | `{ tracked }` |

---

## 16 · Public (no auth)
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/plans` | 🔓 | Public pricing | – | `[ plan ]` |
| GET | `/blog` | 🔓 | Published posts (paginated) | `?page&limit&status` | `[ blogPost ]` |
| GET | `/blog/{slug}` | 🔓 | Single published post | – | `blogPost` |
| POST | `/contact` | 🔓 | Contact form | `{ name, email, subject, message }` | `{ received }` |
| POST | `/ai-visibility/demo` | 🔓 (5/hr per IP) | Personalised dummy AI scan | `{ url }` | `{ url, overallScore, models, recommendations }` |

---

## 17 · Admin auth — `/api/admin/auth`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| POST | `/admin/auth/login` | 🔓 (5/30min lockout) | Login with `jalwa`/`jalwaadmin` (configurable). Sets HttpOnly cookie + returns token | `{ username, password }` | `{ token, expiresAt }` |
| POST | `/admin/auth/logout` | 🟥 | Destroy session | – | `{ loggedOut }` |
| GET | `/admin/auth/verify` | 🟥 | Confirm session valid | – | `{ valid, expiresAt }` |

---

## 18 · Admin dashboard — `/api/admin/dashboard`
| Method | Path | Auth | What it does | Data |
| --- | --- | --- | --- | --- |
| GET | `/admin/dashboard/stats` | 🟥 | KPIs | `{ totalUsers, paidUsers, freeUsers, MRR, churnThisMonth, newSignupsToday, newSignupsThisWeek, newSignupsThisMonth, planDistribution }` |
| GET | `/admin/dashboard/activity` | 🟥 | Recent events feed | `[ event ]` |

---

## 19 · Admin users — `/api/admin/users`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/users` | 🟥 | Paginated, search, plan filter | `?page&limit&search&plan&status` | `[ userWithSubscription ]` |
| GET | `/admin/users/{id}` | 🟥 | Full profile | – | `{ user, subscription, invoices, sites, socialAccounts, usage }` |
| PUT | `/admin/users/{id}/plan` | 🟥 | Switch plan | `{ planId }` | `{ updated }` |
| PUT | `/admin/users/{id}/status` | 🟥 | Suspend / reactivate | `{ status }` | `{ updated }` |
| POST | `/admin/users/{id}/extend-trial` | 🟥 | Add trial days | `{ days }` | `{ trialEndsAt }` |
| POST | `/admin/users/{id}/note` | 🟥 | Internal note | `{ note }` | `{ saved }` |
| GET | `/admin/users/{id}/activity` | 🟥 | User activity log (paginated) | `?page&limit` | `[ event ]` |

---

## 20 · Admin plans — `/api/admin/plans`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/plans` | 🟥 | All plans | – | `[ plan ]` |
| POST | `/admin/plans` | 🟥 | Create plan | full plan body | `plan` |
| PUT | `/admin/plans/{id}` | 🟥 | Update plan (live on public pricing) | partial plan body | `{ updated }` |
| DELETE | `/admin/plans/{id}` | 🟥 | Soft delete (sets `isActive=false`) | – | `{ deleted }` |

---

## 21 · Admin billing — `/api/admin/billing`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/billing/overview` | 🟥 | Revenue summary | – | `{ MRR, revenueThisMonth, revenueLastMonth, growthPercent, failedPayments }` |
| GET | `/admin/billing/transactions` | 🟥 | Invoices (paginated, `status`, `dateRange`) | – | `[ invoice ]` |
| POST | `/admin/billing/refund/{invoiceId}` | 🟥 | Refund via LemonSqueezy (mock) | – | `{ refunded, refundId }` |

---

## 22 · Admin coupons — `/api/admin/coupons`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/coupons` | 🟥 | List | – | `[ coupon ]` |
| POST | `/admin/coupons` | 🟥 | Create | `{ code, type, value, duration?, maxUses?, expiresAt? }` | `coupon` |
| PUT | `/admin/coupons/{id}` | 🟥 | Toggle active | `{ isActive }` | `{ updated }` |
| DELETE | `/admin/coupons/{id}` | 🟥 | Delete | – | `{ deleted }` |

`type` ∈ `PERCENTAGE · FIXED` &nbsp;·&nbsp; `duration` ∈ `ONCE · REPEATING · FOREVER` (default `ONCE`)

---

## 23 · Admin blog — `/api/admin/blog`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/blog` | 🟥 | List (paginated, `status`) | – | `[ blogPost ]` |
| GET | `/admin/blog/{id}` | 🟥 | Get post | – | `blogPost` |
| POST | `/admin/blog` | 🟥 | Create (auto-slug) | `{ title, content, excerpt?, featuredImageUrl?, metaTitle?, metaDescription?, status?, publishedAt? }` | `blogPost` |
| PUT | `/admin/blog/{id}` | 🟥 | Update | partial body | `{ updated }` |
| DELETE | `/admin/blog/{id}` | 🟥 | Delete | – | `{ deleted }` |

---

## 24 · Admin announcements — `/api/admin/announcements`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| POST | `/admin/announcements` | 🟥 | Broadcast (in-app and/or email) | `{ subject, message, targetPlan, channel }` | `announcement` |
| GET | `/admin/announcements` | 🟥 | History | – | `[ announcement ]` |

`targetPlan` ∈ `ALL · FREE · STARTER · GROWTH · AGENCY` &nbsp;·&nbsp; `channel` ∈ `IN_APP · EMAIL · BOTH`

---

## 25 · Admin analytics — `/api/admin/analytics`
| Method | Path | Auth | What it does | Data |
| --- | --- | --- | --- | --- |
| GET | `/admin/analytics/users` | 🟥 | User-growth chart (`?dateRange=30d`) | `[ { date, count } ]` |
| GET | `/admin/analytics/revenue` | 🟥 | Revenue chart | `[ { date, revenue } ]` |
| GET | `/admin/analytics/modules` | 🟥 | Feature-usage counters | `{ articles, socialPosts, aiScans, generatedContent, brandVoices }` |
| GET | `/admin/analytics/funnel` | 🟥 | Visitors → signups → trial → paid | `{ visitors, signups, trial, paid }` |

---

## 26 · Admin API keys — `/api/admin/api-keys`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/api-keys` | 🟥 | List configured keys (masked last 4) | – | `[ { key, maskedValue, isActive, lastTestedAt, testStatus, updatedAt } ]` |
| GET | `/admin/api-keys/supported` | 🟥 | All supported service names | – | `[ "openai", "resend", ... ]` |
| POST | `/admin/api-keys` | 🟥 | Add/update encrypted key | `{ key, value }` | `{ key }` |
| PUT | `/admin/api-keys/{key}` | 🟥 | Update value | `{ value }` | `{ updated }` |
| POST | `/admin/api-keys/{key}/test` | 🟥 | Live test (real OpenAI call for `openai`, mocked others) | – | `{ success, message }` |

Supported keys: `openai · anthropic · gemini · perplexity · resend · dataforseo · r2_account_id · r2_access_key_id · r2_secret_access_key · lemonsqueezy_api_key · lemonsqueezy_store_id · meta_app_id · meta_app_secret · linkedin_client_id · linkedin_client_secret · twitter_client_id · twitter_client_secret · pinterest_app_id · pinterest_app_secret · google_client_id · google_client_secret`

---

## 27 · Admin settings — `/api/admin/settings`
| Method | Path | Auth | What it does | Body | Data |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/settings` | 🟥 | Get general settings | – | `settings` |
| PUT | `/admin/settings` | 🟥 | Update settings | `{ siteName?, siteUrl?, supportEmail?, contactEmail?, twitterUrl?, linkedinUrl?, instagramUrl?, maintenanceMode?, maintenanceMessage? }` | `{ updated }` |
| PUT | `/admin/settings/password` | 🟥 | Change admin password | `{ currentPassword, newPassword }` | `{ updated }` |
