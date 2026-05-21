# SEO Jalwa — Frontend Builder Brief
**Backend status:** Phase 1 complete. All endpoints below are live on
`process.env.REACT_APP_BACKEND_URL` (already configured in `frontend/.env`).
Auth uses JWT (Bearer token) for users and `X-Admin-Token` for the admin panel.

---

## 1. Auth & profile (user side)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register` | `{fullName,email,password,websiteUrl?}` → `{user, accessToken, refreshToken, sites: [auto-created Site if websiteUrl]}` |
| POST | `/api/auth/login` | `{email,password}` → tokens |
| GET  | `/api/auth/google` | **Full-page redirect** to Google consent |
| GET  | `/api/auth/google/callback` | Backend redirects to `${FRONTEND_URL}/auth/google/callback?accessToken=…&refreshToken=…&isNewUser=…` |
| POST | `/api/auth/forgot-password` | `{email}` → always success |
| POST | `/api/auth/reset-password` | `{token,newPassword}` |
| POST | `/api/auth/refresh` | `{refreshToken}` |
| GET  | `/api/auth/me` | `{user, subscription:{…, plan:{…}}, sites:[]}` |
| PUT  | `/api/user/profile` | `{fullName?,email?,websiteUrl?}` — propagates websiteUrl change to matching Site |
| PUT  | `/api/user/password` | `{currentPassword,newPassword}` |

**Token storage**: localStorage `accessToken` + `refreshToken`. Attach
`Authorization: Bearer ${accessToken}` to all authenticated requests.

**Required pages**:
- `/login`, `/signup` — "Continue with Google" button does `window.location.href = `${API}/api/auth/google``
- `/auth/google/callback` — reads `accessToken`, `refreshToken`, `isNewUser` from query string, stores tokens, calls `/api/auth/me`, then routes to `/dashboard` (with onboarding when `isNewUser=true`)
- `/forgot-password`, `/reset-password/:token`

---

## 2. Sites & WordPress plugin

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/sites` | array (each item has `apiKey`) |
| POST | `/api/sites` | `{name,url,platform:'WORDPRESS'|…}` |
| GET  | `/api/sites/{id}` | includes `apiKey` (only to owner) |
| POST | `/api/sites/{id}/verify-connection` | HTTP probe to `{site.url}/wp-json/seojalwa/v1/status` |
| POST | `/api/sites/migrate-from-profile` | One-time backfill |
| GET  | `/api/plugin/version` | `{version, download_url, changelog, …}` |

**Dashboard UX**:
- After signup, show a "Connect your WordPress" step with the API key
  (`jalwa_live_…`) and a download button pointing at
  `GET /api/plugin/version` → `download_url`.
- Display "Connected ✓" badge based on `site.wordpressConnected`.

---

## 3. Articles & search terms

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/articles?siteId=` | paginated list |
| POST | `/api/articles/generate` | `{siteId, searchTerm}` → `{jobId, quota:{used,limit,unlimited}}` or `403 LIMIT_REACHED` with `meta.upgrade_url` |
| GET  | `/api/articles/job/{jobId}` | `{status,progress:0-100,articleId?,error?}` — poll every 2-3s |
| GET  | `/api/articles/calendar?siteId=&year=&month=` | grouped by date |
| GET  | `/api/article-settings/{siteId}` | always returns object (defaults) |
| PUT  | `/api/article-settings/{siteId}` | upsert |
| GET  | `/api/search-terms?siteId=` | list |
| POST | `/api/search-terms` | `{siteId, terms:[…]}` |

**LIMIT_REACHED UX**: catch `{code:"LIMIT_REACHED", meta:{used,limit,upgrade_url}}` and show "Upgrade to keep generating" modal.

---

## 4. AI Visibility & Growth Score

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/ai-visibility/latest?siteId=` | most recent scan |
| POST | `/api/ai-visibility/scan` | `{siteId}` → `{jobId}` |
| GET  | `/api/growth-score?siteId=` | computed |

**Simulated providers**: each model in `rawResults` has `simulated: bool` and `note`. Show an "ℹ️ Simulated" pill for those rows so users know to add their real Anthropic/Gemini/Perplexity keys (or wait for ours).

---

## 5. Notifications (FIX 8) ⭐ NEW

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/notifications?page=&limit=&unread_only=` | paginated list |
| GET  | `/api/notifications/unread-count` | `{count}` — drive the bell badge |
| POST | `/api/notifications/{id}/read` | mark single |
| POST | `/api/notifications/read-all` | mark all |

**Notification shape**:
```ts
type Notification = {
  id: string; type: 'ARTICLE_PUBLISHED'|'ARTICLE_FAILED'|'AI_SCAN_COMPLETE'|
    'LOW_GROWTH_SCORE'|'TRIAL_ENDING'|'PAYMENT_FAILED'|'ANNOUNCEMENT'|…;
  title: string; message: string;
  icon: string;   // optional lucide-icon name
  link: string;   // deep link inside the app
  read: boolean; createdAt: string;
};
```

**UX**: poll `/unread-count` every 60s, show red dot on bell icon; popover renders the list and "Mark all as read".

---

## 6. User activity log (FIX 4) ⭐ NEW

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/user/activity?page=&limit=&action=` | own activity feed |

Use this on a `/settings/security` or `/settings/activity` tab so users can see recent logins, profile updates, feedback submissions, etc.

---

## 7. Feedback (FIX 10) ⭐ NEW

| Method | Path | Notes |
|---|---|---|
| POST | `/api/feedback` | `{message, rating?(1-5), category?, pageUrl?, email?}` (email required if not logged in) |

Place a floating "💬 Feedback" widget on `/dashboard` that opens a small modal.

---

## 8. Admin panel (X-Admin-Token header)

Admin login: `POST /api/admin/auth/login` with `{username:"jalwa",password:"jalwaadmin"}` → `{token}`. Save in admin localStorage.

| Method | Path | Notes |
|---|---|---|
| GET  | `/api/admin/dashboard/stats` | `totalUsers, paidUsers, freeUsers, MRR, ARR, churnThisMonth, churnCount, newSignupsToday/Week/Month, articlesGeneratedToday/Month, scansRunToday, emailsSentToday, planDistribution` |
| GET  | `/api/admin/dashboard/activity` | recent events |
| GET  | `/api/admin/users?page=&limit=&search=` | paginated |
| GET  | `/api/admin/users/{id}` | full detail |
| **DELETE** | **`/api/admin/users/{id}`** | **Cascade delete (FIX 9)** — returns `{deletedUser, cascadedDeletes}` |
| PUT  | `/api/admin/users/{id}/plan` | legacy plan-only change |
| PUT  | `/api/admin/users/{id}/subscription` | `{planId?, status?, billingInterval?, trialDays?, adminNote?}` — rich |
| PUT  | `/api/admin/users/{id}/status` | suspend/activate |
| POST | `/api/admin/users/{id}/extend-trial` | `{days}` |
| POST | `/api/admin/users/{id}/note` | `{note}` |
| **GET**  | **`/api/admin/users/{id}/activity-log`** | **paginated activity (FIX 4)** |
| GET  | `/api/admin/audit-log?page=&limit=&action=&target_id=` | all admin actions |
| **GET**  | **`/api/admin/emails`** | **email logs (FIX 5)** — filters: `status, user_id, template_key` |
| **GET**  | **`/api/admin/emails/{id}`** | **single email** |
| **GET**  | **`/api/admin/email-templates`** | **list 15 templates (FIX 6)** |
| **GET**  | **`/api/admin/email-templates/{key}`** | **one template** |
| **PUT**  | **`/api/admin/email-templates/{key}`** | `{subject?, htmlBody?, isActive?, …}` |
| **POST** | **`/api/admin/email-templates/{key}/test`** | `{testEmail}` |
| **POST** | **`/api/admin/email-templates/seed`** | manual re-seed |
| **GET**  | **`/api/admin/submissions?type=&status=`** | **contact + feedback (FIX 10)** |
| **GET**  | **`/api/admin/submissions/{id}`** | full record |
| **PUT**  | **`/api/admin/submissions/{id}`** | `{status?, adminNotes?}` |
| **POST** | **`/api/admin/submissions/{id}/reply`** | `{message}` — sends real email |
| **GET**  | **`/api/admin/insights/retention[?force=true]`** | **GPT suggestions + metrics (FIX 11)** |
| GET  | `/api/admin/api-keys` | 14 services, rich shape |
| GET/PUT | `/api/admin/api-keys/{key}` | accepts `{value}` or `{fields}` |
| POST | `/api/admin/api-keys/{key}/test` | live integration test |
| GET/PUT | `/api/admin/settings` | now includes `renewalReminderDays`, `trialEndingReminderDays`, `paymentRetryDays` arrays plus plugin distribution fields |
| POST | `/api/admin/announcements` | `channel: 'EMAIL'|'IN_APP'|'BOTH'`, `targetPlan: 'ALL'|'FREE'|'STARTER'|'GROWTH'|'AGENCY'` |
| POST | `/api/admin/plugin/upload` | multipart `.zip` |
| GET  | `/api/admin/plugin/info` | `{version, download_url, changelog}` |
| GET  | `/api/admin/plans` `/coupons` `/blog` `/analytics/{users|revenue|modules|funnel}` `/billing/overview` | already wired |

---

## 9. Suggested admin-panel pages

1. **Dashboard** — pull `/api/admin/dashboard/stats` + `/api/admin/dashboard/activity` + `/api/admin/insights/retention`.
2. **Users** — table with cascade-delete, "Manage subscription" drawer, "View activity log" link.
3. **Subscriptions** — uses `PUT /api/admin/users/{id}/subscription`.
4. **Email Templates** — left rail (15 templates), right pane = subject + Monaco/CodeMirror HTML editor + "Send test" button.
5. **Email Logs** — table with filters (status, template, user, date).
6. **Audit Log** — read-only, filter by action/targetId.
7. **Submissions** — Inbox-style; FEEDBACK + CONTACT tabs, status pills, "Reply" composes email.
8. **AI Insights** — `/admin/insights/retention` rendered as a stacked card per suggestion (priority colour, effort badge, recommendation paragraph).
9. **Settings** — three sections: General, Plugin Distribution, Reminders.
10. **API Keys** — already exists; uses the 14-service rich shape.
11. **Plans / Coupons / Blog / Announcements / Plugin Upload** — straightforward CRUD pages.

---

## 10. Error envelope (every endpoint)

```json
{ "success": false,
  "error": "Article limit reached for the current period",
  "code": "LIMIT_REACHED",
  "statusCode": 403,
  "meta": { "resource":"articles","used":20,"limit":20,
            "upgrade_url":"/pricing" } }
```

Build a shared `apiClient` that surfaces `code`, `statusCode`, and `meta`
so individual screens can render upgrade prompts, "trial ending" toasts,
etc. without parsing strings.

---

## 11. Theming / brand

- Use **shadcn/ui** primitives already in `frontend/src/components/ui/`.
- Avoid AI-slop purple gradients — pick a sharp accent (suggest emerald `#10B981` for "growth" or amber for "alerts").
- Don't use emoji as icons; use `lucide-react` (already in `package.json`).
- Required `data-testid` on every interactive element (see system prompt).

---

## 12. Going live checklist (frontend ↔ Railway)

1. `REACT_APP_BACKEND_URL=https://api.seojalwa.com` already set.
2. Make sure all API calls go through `${process.env.REACT_APP_BACKEND_URL}/api/…` — never hard-code.
3. After login, redirect to `/dashboard` and fetch `/api/auth/me` to hydrate auth context with `{user, subscription, sites}`.
4. Listen for `LIMIT_REACHED` and `NO_SUBSCRIPTION` codes to show upgrade modals.
5. For "Continue with Google", do a full-page redirect to `/api/auth/google` — popup OAuth won't work because the callback redirects to our frontend URL.
