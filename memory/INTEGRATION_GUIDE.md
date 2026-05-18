# SEO Jalwa — Frontend ↔ Backend Integration Guide

This document explains how the React frontend should consume the SEO Jalwa REST API.

---

## 1. Base URL

- **Local dev**: `http://localhost:8001`
- **Production (this environment)**: read from `process.env.REACT_APP_BACKEND_URL`
  → currently `https://growth-engine-api.preview.emergentagent.com`

Every backend route is prefixed with `/api`. The only exception is the local-only
`/health` (the Kubernetes ingress strips non-`/api` paths). Use `/api/health` for
public health checks.

```js
// src/lib/api.js
export const BASE_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BASE_URL}/api`;
```

> ⚠️ Do **not** hardcode the URL. Always read it from
> `process.env.REACT_APP_BACKEND_URL`.

---

## 2. Response envelope

Every JSON response uses the same shape.

### Success
```json
{
  "success": true,
  "data": { ... } | [ ... ] | null,
  "message": "Human-readable message",
  "pagination": { "total": 120, "page": 1, "limit": 20, "totalPages": 6 }
}
```
`pagination` is only present on list endpoints.

### Error
```json
{
  "success": false,
  "error": "Email already registered",
  "code": "EMAIL_TAKEN",
  "statusCode": 409,
  "details": [ ... ]
}
```
`details` is only present on `422 VALIDATION_ERROR` and contains the per-field
Pydantic errors.

### Universal fetch helper
```js
// src/lib/api.js
export async function apiFetch(path, { method = "GET", body, headers = {}, auth = true } = {}) {
  const token = localStorage.getItem("accessToken");
  const finalHeaders = {
    "Content-Type": "application/json",
    ...(auth && token ? { Authorization: `Bearer ${token}` } : {}),
    ...headers,
  };

  const res = await fetch(`${API}${path}`, {
    method,
    headers: finalHeaders,
    body: body ? JSON.stringify(body) : undefined,
    credentials: "include", // needed for admin session cookie
  });

  const json = await res.json().catch(() => ({
    success: false, error: "Invalid JSON", code: "PARSE_ERROR", statusCode: res.status,
  }));

  if (!json.success) {
    // Hook 401 handling to a single place
    if (json.statusCode === 401 && json.code === "UNAUTHORIZED") {
      await tryRefreshOrLogout();
    }
    throw new ApiError(json);
  }
  return json; // caller reads .data, .pagination, .message
}

export class ApiError extends Error {
  constructor({ error, code, statusCode, details }) {
    super(error);
    this.code = code;
    this.statusCode = statusCode;
    this.details = details;
  }
}
```

---

## 3. User authentication (JWT)

### Register / Login / Google
```js
const { data } = await apiFetch("/auth/register", {
  method: "POST", auth: false,
  body: { fullName, email, password, websiteUrl },
});
localStorage.setItem("accessToken", data.accessToken);
localStorage.setItem("refreshToken", data.refreshToken);
```

Server returns:
```json
{
  "success": true,
  "data": {
    "user": { "id": "...", "email": "...", "fullName": "...", ... },
    "accessToken": "eyJhbGci...",
    "refreshToken": "eyJhbGci..."
  }
}
```

Store both tokens. Access tokens are short-lived (15 m); refresh tokens last 7 d.

### Sending JWT
On every authenticated request:
```
Authorization: Bearer <accessToken>
```
The helper above does this automatically.

### Refresh flow
When you receive `401 UNAUTHORIZED` from any endpoint:

```js
async function tryRefreshOrLogout() {
  const refreshToken = localStorage.getItem("refreshToken");
  if (!refreshToken) return logoutAndRedirect();
  try {
    const res = await fetch(`${API}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refreshToken }),
    });
    const json = await res.json();
    if (!json.success) throw new Error(json.error);
    localStorage.setItem("accessToken", json.data.accessToken);
  } catch {
    logoutAndRedirect();
  }
}

function logoutAndRedirect() {
  localStorage.removeItem("accessToken");
  localStorage.removeItem("refreshToken");
  window.location.assign("/login");
}
```

A common pattern is to wrap `apiFetch` so that **one** 401 triggers a refresh,
retries the original request once, and only logs out on a second failure.

### Logout
```js
await apiFetch("/auth/logout", { method: "POST" });
localStorage.removeItem("accessToken");
localStorage.removeItem("refreshToken");
```

### Current user
```js
const { data } = await apiFetch("/auth/me");
// data = { user, subscription, sites }
```

---

## 4. Admin authentication (session)

Admin uses a separate flow at `/api/admin/*` with **two** equivalent transport options:

### Option A — cookie (browser-style)
```js
const res = await fetch(`${API}/admin/auth/login`, {
  method: "POST",
  credentials: "include", // CRITICAL
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "jalwa", password: "jalwaadmin" }),
});
// `admin_session` HttpOnly cookie now stored. Subsequent
// fetches to /api/admin/* with `credentials: "include"` will send it.
```

### Option B — header (SPA / cross-origin friendly)
The login response also returns the raw token in the body:
```json
{ "success": true, "data": { "token": "abc...", "expiresAt": "..." } }
```
Persist that token (e.g. `sessionStorage.setItem("adminToken", ...)`) and send it
on every admin request:
```
X-Admin-Token: <token>
```
This is the **preferred** method when the frontend is hosted on a different
origin from the API, because some cookie attributes (`SameSite=Lax`) cannot
cross sites.

### Verify / logout
```
GET  /api/admin/auth/verify   → { valid: true, expiresAt }
POST /api/admin/auth/logout
```

Sessions expire after 2 hours.

---

## 5. CORS

CORS is already configured on the backend:
```
allow_origins  = CORS_ORIGINS env (currently "*")
allow_methods  = *
allow_headers  = *
allow_credentials = true
```

When the frontend goes to production:
1. Set `CORS_ORIGINS` in `backend/.env` to your real domain
   (e.g. `https://app.seojalwa.com`)
2. Restart the backend service

Until then, `*` is permissive and works with any origin.

---

## 6. Pagination

List endpoints accept `?page=` and `?limit=` query params (defaults `page=1`,
`limit=20`).

The response contains a `pagination` object:
```json
{
  "data": [ ... ],
  "pagination": { "total": 120, "page": 1, "limit": 20, "totalPages": 6 }
}
```

Render UI like:
```jsx
<Paginator
  total={pagination.total}
  page={pagination.page}
  totalPages={pagination.totalPages}
  onChange={(p) => loadPage(p)}
/>
```

---

## 7. Background jobs (polling)

Three endpoints kick off background work and return a `jobId`:

| Endpoint | Poll URL |
| --- | --- |
| `POST /api/articles/generate` | `GET /api/articles/job/{jobId}` |
| `POST /api/ai-visibility/scan` | `GET /api/ai-visibility/scan/{jobId}` |
| `POST /api/brand-voice/train` | `GET /api/articles/job/{jobId}` (same jobs collection) |

Poll every 2–5 seconds until `data.status === "completed"` or `"failed"`.

```js
async function pollJob(jobUrl, onProgress) {
  while (true) {
    const { data } = await apiFetch(jobUrl);
    onProgress?.(data.progress, data.status);
    if (data.status === "completed") return data.result;
    if (data.status === "failed") throw new Error(data.error || "Job failed");
    await new Promise((r) => setTimeout(r, 3000));
  }
}
```

---

## 8. Error code cheat-sheet

| HTTP | code | When |
| --- | --- | --- |
| 400 | `INVALID_*`, `INVALID_PLATFORM`, `INVALID_ROLE`, `INVALID_TYPE` | Bad request payload |
| 401 | `UNAUTHORIZED` | Missing/expired user JWT — trigger refresh flow |
| 401 | `ADMIN_UNAUTHORIZED` | Missing/expired admin session — re-login |
| 401 | `INVALID_CREDENTIALS` | Wrong password during login or password change |
| 401 | `INVALID_TOKEN` / `TOKEN_EXPIRED` | Reset / verify / refresh token bad |
| 404 | `NOT_FOUND` | Resource not found |
| 409 | `EMAIL_TAKEN` | Register on duplicate email |
| 422 | `VALIDATION_ERROR` | Pydantic validation; check `details[]` |
| 429 | `RATE_LIMITED` | Per-IP rate limit (auth endpoints, public AI demo, etc.) |
| 429 | `LOCKED_OUT` | 5 failed admin logins → locked 30 min |
| 500 | `INTERNAL_ERROR` | Unhandled server error |

---

## 9. File uploads

Currently the backend exposes **no** direct upload endpoint — image generation
is done server-side and stored at a URL returned in `featuredImageUrl` /
`imageUrl`. When real Cloudflare R2 is wired (see PRD P0), a signed-URL upload
flow will be added.

---

## 10. WebSockets

Not used in v1. All async features (job status, scan progress) are polled via
the `GET /job/{id}` endpoints above. A WebSocket gateway is listed as P2 in
the PRD if needed later.

---

## 11. Suggested folder layout in the React app

```
/src
  /lib
    api.js         ← BASE_URL + apiFetch + ApiError (above)
    auth.js        ← login/register/logout/refresh helpers
  /context
    AuthContext.js ← provides user, tokens, login(), logout()
  /pages
    Login.jsx
    Register.jsx
    Dashboard/
    Articles/
    SocialPosts/
    AiVisibility/
    Settings/
  /pages/admin
    AdminLogin.jsx
    AdminDashboard.jsx
    ...
```

---

## 12. Quick start checklist

- [ ] Set `REACT_APP_BACKEND_URL` in `frontend/.env` (already done)
- [ ] Implement `src/lib/api.js` exactly as shown above
- [ ] Add an `AuthContext` that loads tokens from `localStorage` on boot and
      calls `GET /auth/me`
- [ ] Wrap every protected route in `<RequireAuth>` redirecting to `/login` on
      a thrown `ApiError` with `statusCode === 401`
- [ ] On `mount`, call `GET /api/plans` for the public pricing page
- [ ] For the admin panel, use a **separate** `AdminAuthContext` with the
      `X-Admin-Token` header method described in section 4 Option B

You're ready to plug the React frontend into the backend.
