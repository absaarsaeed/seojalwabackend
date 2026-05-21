# SEO Jalwa — Frontend Polish & Launch Brief (v2)
**For the frontend agent.** Backend is fully ready. This document covers the
20-part "Final frontend polish" requirements and the endpoints that back
each one.

> Base URL: `process.env.REACT_APP_BACKEND_URL` (already in `frontend/.env`).
> Auth header: `Authorization: Bearer <accessToken>` for users,
> `X-Admin-Token: <token>` for admin.

---

## PART 1 — Dashboard real data → `GET /api/dashboard/overview?siteId=…`

Single endpoint that drives the whole `/dashboard` home. Response shape:

```jsonc
{
  "data": {
    "site": {
      "id": "…", "name": "…", "url": "…", "platform": "WORDPRESS",
      "wordpressConnected": true, "analyzed": false
    },
    "subscription": { /* full sub with .plan populated, or null */ },
    "trial": {                                  // null when not trialing
      "status": "TRIALING",
      "daysLeft": 9, "totalDays": 14, "daysUsed": 5,
      "endsAt": "2026-…", "urgent": false, "expired": false
    },
    "metrics": {
      "visibilityScore": 78,                     // ring on the AI Visibility card
      "articlesThisMonth": 7, "articlesLastMonth": 4, "articlesDelta": 3,
      "socialPostsScheduled": 2,
      "totalClicks": 1240, "totalImpressions": 18900,
      "growthScore": 62
    },
    "recommendations": [                         // up to 3, ordered by impact
      { "id": "connect-wp", "icon": "plug",
        "category": "setup", "title": "Connect WordPress",
        "description": "…", "ctaLabel": "Connect now",
        "ctaUrl": "/dashboard/connections" }
    ],
    "recentActivity": [                          // up to 8
      { "id": "…", "action": "ARTICLE_PUBLISHED",
        "metadata": { "articleId": "…", "title": "…" },
        "createdAt": "2026-…" }
    ]
  }
}
```

**Card click targets**:
- Visibility → `/dashboard/ai-visibility`
- Articles → `/dashboard/auto-publish`
- Clicks → `/dashboard/analytics`
- Social → `/dashboard/social-autopilot`

**Trial banner**: if `data.trial != null`, render a sticky banner with the
progress bar; intensify styling when `trial.urgent === true` and show a
block-state when `trial.expired === true`.

---

## PART 2 — "Coming Soon" on Connections & Social
Use `GET /api/pages/integrations` for the catalogue:

```jsonc
{
  "data": {
    "categories": [
      { "name": "Website Platforms",
        "items": [{ "key":"wordpress","name":"WordPress","isAvailable":true,
                    "description":"…","logo":"wordpress" }, … ] },
      { "name": "Social Autopilot", "items": [ … ] }
    ],
    "platforms": [ /* flat list, same items */ ]
  }
}
```

If `item.isAvailable === false`:
- `opacity: 0.6`, dashed border or grey pill `Available in v2`
- `cursor: not-allowed`, click does nothing (no navigation)

Currently only WordPress is `isAvailable: true`.

---

## PART 3 — Auto-setup notification after WP connect
Poll `GET /api/sites/{id}` every 3 s after `POST /api/sites/{id}/verify-connection`. Stop when `site.analyzed === true` (the cron will flip
this once `site.wordpressConnected` plus analysis settles).

When that flips:
1. Show success modal with the two CTAs from the brief.
2. Trigger a refresh of `/api/notifications/unread-count` (a `SITE_CONNECTED` notification is created server-side via the activity hook).
3. Mark onboarding step complete in your local state.

Until the cron is configured, you can treat `site.wordpressConnected === true` as the trigger and skip the `analyzed` check.

---

## PART 4 — Pre-generated trial articles
`GET /api/articles?siteId={id}&status=SCHEDULED` — render the existing
calendar view. Pre-generation is a Phase-2 background job (run by the admin
when activating a paid plan); for the trial cohort you can rely on the
"Generate your first article" recommendation from `/dashboard/overview`.

When `articles.length > 0`, show the banner:
> "We pre-generated {N} articles for your trial. Click any article to review and edit before publishing."

Each card: title, excerpt (first 100 chars of `content`), SEO badge (`seoScore`), status badge (`status`), scheduled date (`scheduledAt`), "View & Edit" → `/dashboard/auto-publish/article/:id`.

---

## PART 5 — Article view & edit page
Endpoints already live:
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/articles/{id}` | full article (title, content, seoScore, metaTitle, metaDescription, scheduledAt, category, keyTakeaways, faq, tags, featuredImageUrl) |
| PUT | `/api/articles/{id}` | partial update — `{title?, content?, metaTitle?, metaDescription?, scheduledAt?, …}` |
| POST | `/api/articles/{id}/publish` | publish-to-WP now |
| DELETE | `/api/articles/{id}` | soft delete |

Two-column layout exactly as the brief — use shadcn `Textarea` or any rich-text editor (TipTap recommended).

After publish success show toast `"Published to WordPress!"`, update badge to `PUBLISHED`, and show `wordpressUrl` as a "View on site →" link.

---

## PART 6 — Article failure handling → `POST /api/articles/{id}/retry`
Only callable on `status=FAILED|DRAFT`. Returns `{jobId, articleId, status:"queued", quota}` — re-use the same job-poll loop as fresh generation.

If the user has hit their monthly limit, `/retry` will 403 with `code:"LIMIT_REACHED"` and the upgrade-CTA `meta`.

---

## PART 7 — Naming updates (UI-side string changes)
Find-and-replace across the codebase:
| Old | New |
|---|---|
| "Auto Publish" | "Auto Article Writing" |
| "CMS Connections" | "Website Connections" |
Apply in sidebar, page titles, breadcrumbs, pricing table, settings sections.

---

## PART 8 — Pricing page synced with DB
- `GET /api/plans` for plan rows (`name, monthlyPrice, annualPrice, articlesPerMonth, socialPostsPerMonth, aiScansPerMonth, teamSeats, websiteConnections, whiteLabel`).
- `GET /api/settings/public` for `trial_days` (admin-editable).
- For `whiteLabel:true` → list feature "White-label (no SEO Jalwa branding)". For `false` → "Powered by SEO Jalwa".
- Headline CTA: `Start your {trial_days}-day free trial`.

---

## PART 9 — Blog
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/blog?page=&limit=` | published posts grid |
| GET | `/api/blog/{slug}` | single post (HTML in `content`) |

Card fields: `featuredImage`, `title`, `excerpt`, `publishedAt`, slug → `/blog/{slug}`.

Single post: render `content` (HTML), show `publishedAt`, fallback author `"SEO Jalwa Team"`.

---

## PART 10 — Integrations page → covered by `GET /api/pages/integrations`
See Part 2.

---

## PART 11 — Google Search Console connect
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/analytics/gsc/connect?siteId=…` | returns `{authUrl}` — full-page redirect there |
| GET | `/api/analytics/gsc/callback` | backend handles Google OAuth and redirects back |
| GET | `/api/analytics/overview?siteId=…&range=…` | actual GSC metrics (impressions, clicks, ctr, position, top articles, trend) |

After the OAuth round trip the URL will include `?connected=true` — show the success toast, then show a loading state while you call `/api/analytics/overview`. The first call may take a few seconds because the backend syncs GSC in the background; poll every 5 s until `data.synced === true`.

---

## PART 12 — Trial countdown UI
Drive everything from `dashboard.trial`:
- Banner at the top of `/dashboard` when `trial != null`.
- More urgent style when `trial.urgent === true` (≤ 3 days).
- On `trial.expired === true`, block the `POST /api/articles/generate` button; the backend will return `403 NO_SUBSCRIPTION` if they try anyway.
- Settings → Billing tab mirrors the same banner and links to `/pricing`.

---

## PART 13 — Plan upgrade flow
- "Upgrade" buttons everywhere → `/pricing`.
- On `/pricing` click "Get started" of a plan → `POST /api/billing/checkout` with `{planId, interval:"MONTHLY"|"ANNUAL"}`.
- Until LemonSqueezy is wired, the response will look like `{success:true, data:{checkoutUrl:null, contactRequired:true, message:"Contact us to upgrade"}}`. Render a modal with the `hello@seojalwa.com` mailto link.
- Once admin manually upgrades via `PUT /api/admin/users/{id}/subscription`, the user's next `/api/auth/me` call returns the new `plan` populated; show a one-off welcome toast.

---

## PART 14 — Notifications wired
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/notifications/unread-count` | poll every 60 s |
| GET | `/api/notifications?limit=10` | bell dropdown |
| GET | `/api/notifications?page=&limit=&unread_only=` | full page |
| POST | `/api/notifications/{id}/read` | mark single |
| POST | `/api/notifications/read-all` | mark all |

Use the `link` field as the deep target. Type → icon mapping is up to the UI (`ARTICLE_PUBLISHED → file-text`, `ARTICLE_FAILED → alert-triangle`, `AI_SCAN_COMPLETE → scan`, `TRIAL_ENDING → clock`, `ANNOUNCEMENT → megaphone`, …).

---

## PART 15 — Admin trial-days setting
`GET /api/admin/settings` now returns `trialDays`.
`PUT /api/admin/settings` accepts `{trialDays: <number>}`.

Add the field to the "General" section of the admin settings page with helper text:
> "New users get this many days of free trial."

Backend auto-creates each new user's trial with this number (see `/api/auth/register`).

---

## PART 16 — White-label display
On plan cards:
- `whiteLabel === false` → footer feature row: "Includes SEO Jalwa branding".
- `whiteLabel === true` → "White-label (no branding)" pill.

---

## PART 17 — Recent activity timeline
Already in `dashboard.recentActivity` (see Part 1). Map each `action` to:
| action | label template | icon |
|---|---|---|
| ARTICLE_PUBLISHED | "Published: {metadata.title}" | file-check |
| ARTICLE_FAILED | "Article failed: {metadata.searchTerm}" | alert-triangle |
| AI_SCAN_RUN | "AI scan completed" | scan |
| SEARCH_TERMS_ADDED | "Added {metadata.count} keywords" | hash |
| SETTINGS_UPDATED | "Settings updated" | sliders |
| SITE_CONNECTED | "Connected {metadata.platform}" | plug |
| USER_LOGGED_IN | "Logged in" | log-in |

---

## PART 18 — Global button hover states
Define once in `index.css` (Tailwind utility classes are fine):

```css
.btn-primary { @apply bg-[#1D9E75] text-white transition-all duration-200; }
.btn-primary:hover { @apply bg-[#0F6E56] -translate-y-0.5 shadow-lg; }
.btn-primary:active { @apply bg-[#0A5340]; }
.btn-primary:disabled { @apply bg-gray-200 text-gray-400 cursor-not-allowed transform-none shadow-none; }

.btn-secondary { @apply bg-white border border-gray-300 text-gray-700 transition-all duration-200; }
.btn-secondary:hover { @apply bg-gray-50 border-gray-400; }
.btn-secondary:active { @apply bg-gray-100; }

.btn-danger { @apply bg-red-600 text-white transition-all duration-200; }
.btn-danger:hover { @apply bg-red-700 -translate-y-0.5; }
.btn-danger:active { @apply bg-red-800; }
```

Apply on `/login`, `/signup`, `/forgot-password`, `/reset-password/:token`, and every dashboard / admin button.

---

## PART 19 — Loading states everywhere
- Use shadcn `Skeleton` (already in `components/ui/skeleton.tsx`) for any pending fetch.
- Article generation: poll `/api/articles/job/{jobId}` every 3 s, render a progress bar from `data.progress`. Stage labels:
  - 0 queued, 10 settings, 20 brand voice, 30 research,
    50 content, 70 hero image, 85 R2 upload, 95 WP publish, 100 done.
- AI scan: same job loop. Show one row per model (`chatgpt`, `perplexity`, `gemini`, `claude`, `copilot`) with a per-model progress bar; flip green when finished. Show "ℹ Simulated" pill for any row with `rawResults[model].simulated === true`.

---

## PART 20 — Mobile responsiveness checklist
Tested at 375 px (iPhone SE). Required behaviour:
- `/dashboard` cards stack vertically (`grid-cols-1` < md, `grid-cols-2` md, `grid-cols-4` lg).
- Calendar: swipe between months (use `swiper` or shadcn `Carousel`); each day cell becomes 80×80 min.
- Article editor: stack columns, sticky bottom action bar.
- Settings: single-column forms, full-width inputs.
- Admin tables: wrap in `overflow-x-auto`.

---

## Endpoints added/changed this pass

| ✨ New | Path |
|---|---|
| ✨ | `GET /api/dashboard/overview?siteId=` |
| ✨ | `GET /api/pages/integrations` (public) |
| ✨ | `GET /api/settings/public` (public — `trial_days`, plugin version) |
| ✨ | `POST /api/articles/{id}/retry` |
| ✨ | `PUT /api/admin/settings` now accepts `trialDays` |
| ✨ | `POST /api/auth/register` uses admin's `trial_days` value (default 14) |

All existing endpoints from the previous brief continue to work unchanged.

---

## TL;DR for the frontend agent
1. Hit `GET /api/dashboard/overview?siteId=…` on the dashboard — it returns **all** the metric numbers, the trial banner state, the top-3 recommendations, and the recent-activity timeline in one shot.
2. Replace the static "Coming Soon" arrays with `GET /api/pages/integrations` to drive both the website-platform cards and the social-autopilot cards.
3. Add the article editor at `/dashboard/auto-publish/article/:id` using the four article endpoints (GET, PUT, publish, delete) + retry.
4. Show a per-article "Retry" button on `FAILED` cards.
5. Replace the hard-coded "14-day trial" wording with `GET /api/settings/public → trial_days`.
6. Add `trialDays` to the admin General settings form.
7. Apply the button + skeleton + mobile rules globally — they're framework-wide CSS, not per-page.
