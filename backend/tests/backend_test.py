"""
SEO Jalwa backend regression test-suite.

Covers (alphabetical): admin, ai-visibility, ai-writer, analytics, articles,
article-settings, auth, auto-publish, billing, blog, contact, growth-score,
plans, plugin, public ai-visibility demo (rate-limit last), search-terms,
sites, social, team, user.

All endpoints expect response shape {success, data, message} (and pagination on
lists).  Errors return {success:false, error, code, statusCode}.
"""
from __future__ import annotations
import os, time, uuid, json, pytest, requests
from pathlib import Path

def _load_env():
    env_path = Path("/app/frontend/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

ADMIN_USER = "jalwa"
ADMIN_PASS = "jalwaadmin"

# unique email per run so register tests pass repeatedly
RUN_TAG = uuid.uuid4().hex[:8]
USER_EMAIL = f"tester+{RUN_TAG}@seojalwa.com"
USER_PASS = "Testing12345!"


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"
    return sess


def _expect_shape(j, *, ok=True):
    assert isinstance(j, dict), j
    assert j.get("success") is ok, j


@pytest.fixture(scope="session")
def user_tokens(s):
    r = s.post(f"{API}/auth/register", json={
        "email": USER_EMAIL, "password": USER_PASS,
        "fullName": "Tester", "websiteUrl": "https://t.com",
    })
    assert r.status_code in (200, 201), r.text
    j = r.json(); _expect_shape(j)
    return j["data"]


@pytest.fixture(scope="session")
def auth_headers(user_tokens):
    return {"Authorization": f"Bearer {user_tokens['accessToken']}"}


@pytest.fixture(scope="session")
def admin_token(s):
    # Use unique IP to avoid lockout pollution from prior runs
    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"
    sess.headers["X-Forwarded-For"] = f"10.0.0.{uuid.uuid4().int % 250}"
    r = sess.post(f"{API}/admin/auth/login",
                  json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text
    j = r.json(); _expect_shape(j)
    return j["data"]["token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token):
    return {"X-Admin-Token": admin_token}


# --------------------------------------------------------------------------- #
# Health / Docs / Plans / Blog / Contact                                       #
# --------------------------------------------------------------------------- #
def test_health(s):
    r = s.get(f"{API}/health"); assert r.status_code == 200
    j = r.json(); assert j["status"] == "ok" and j["database"] == "connected"


def test_docs(s):
    r = s.get(f"{API}/docs"); assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()


def test_public_plans(s):
    r = s.get(f"{API}/plans"); assert r.status_code == 200
    j = r.json(); _expect_shape(j)
    names = {p["name"] for p in j["data"]}
    prices = {p.get("monthlyPrice") or p.get("price") for p in j["data"]}
    assert {"Starter", "Growth", "Agency"} <= names, j
    assert {79, 199, 499} <= prices, j


def test_blog_paginated(s):
    r = s.get(f"{API}/blog"); assert r.status_code == 200
    j = r.json(); _expect_shape(j)
    assert "pagination" in j


def test_contact(s):
    r = s.post(f"{API}/contact", json={
        "name": "x", "email": "x@y.com", "subject": "hi", "message": "hello"})
    assert r.status_code in (200, 201), r.text
    _expect_shape(r.json())


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
def test_register_duplicate(s, user_tokens):
    r = s.post(f"{API}/auth/register", json={
        "email": USER_EMAIL, "password": USER_PASS,
        "fullName": "Tester", "websiteUrl": "https://t.com"})
    assert r.status_code == 409, r.text
    j = r.json(); _expect_shape(j, ok=False); assert j["code"] == "EMAIL_TAKEN"


def test_login_wrong(s):
    r = s.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["code"] == "INVALID_CREDENTIALS"


def test_login_ok(s):
    r = s.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": USER_PASS})
    assert r.status_code == 200
    d = r.json()["data"]; assert d["accessToken"] and d["refreshToken"]


def test_refresh(s, user_tokens):
    r = s.post(f"{API}/auth/refresh", json={"refreshToken": user_tokens["refreshToken"]})
    assert r.status_code == 200, r.text
    assert r.json()["data"].get("accessToken")


def test_google_mock(s):
    r = s.post(f"{API}/auth/google", json={"googleToken": "mock-token"})
    assert r.status_code == 200, r.text
    assert r.json()["data"].get("accessToken")


def test_forgot_reset(s):
    r = s.post(f"{API}/auth/forgot-password", json={"email": USER_EMAIL})
    assert r.status_code == 200
    tok = r.json()["data"].get("token") or r.json()["data"].get("resetToken")
    if tok:
        r2 = s.post(f"{API}/auth/reset-password",
                    json={"token": tok, "password": USER_PASS})
        assert r2.status_code == 200, r2.text


def test_me(s, auth_headers):
    r = s.get(f"{API}/auth/me", headers=auth_headers); assert r.status_code == 200
    d = r.json()["data"]; assert "user" in d and "sites" in d


def test_protected_no_token(s):
    r = s.get(f"{API}/auth/me"); assert r.status_code == 401
    assert r.json()["code"] in ("UNAUTHORIZED", "ADMIN_UNAUTHORIZED")


# --------------------------------------------------------------------------- #
# Sites                                                                        #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def site_id(s, auth_headers):
    r = s.get(f"{API}/sites", headers=auth_headers); assert r.status_code == 200
    sites = r.json()["data"]
    if sites:
        return sites[0]["id"]
    r = s.post(f"{API}/sites", headers=auth_headers,
               json={"name": "Site2", "url": "https://s2.com", "platform": "wordpress"})
    return r.json()["data"]["id"]


def test_site_get(s, auth_headers, site_id):
    r = s.get(f"{API}/sites/{site_id}", headers=auth_headers); assert r.status_code == 200


def test_site_update(s, auth_headers, site_id):
    r = s.put(f"{API}/sites/{site_id}", headers=auth_headers,
              json={"name": "Renamed"})
    assert r.status_code == 200, r.text


def test_site_verify_connection(s, auth_headers, site_id):
    r = s.post(f"{API}/sites/{site_id}/verify-connection", headers=auth_headers)
    assert r.status_code == 200
    d = r.json()["data"]; assert "connected" in d


def test_site_connect_cms(s, auth_headers, site_id):
    payloads = {
        "ghost": {"apiKey": "demo", "siteUrl": "https://demo.com"},
        "webflow": {"apiKey": "demo", "siteUrl": "https://demo.com"},
        "hubspot": {"apiKey": "demo", "siteUrl": "https://demo.com"},
        "wix": {"apiKey": "demo", "siteUrl": "https://demo.com"},
        "notion": {"apiKey": "demo", "siteUrl": "https://demo.com"},
    }
    for cms, p in payloads.items():
        r = s.post(f"{API}/sites/{site_id}/connect/{cms}", headers=auth_headers, json=p)
        # accept 200/201, allow 422 if router enforces extra fields per CMS
        assert r.status_code in (200, 201, 422), f"{cms}: {r.status_code} {r.text}"


# --------------------------------------------------------------------------- #
# Search Terms / Article Settings                                              #
# --------------------------------------------------------------------------- #
def test_search_terms_crud(s, auth_headers, site_id):
    r = s.post(f"{API}/search-terms", headers=auth_headers,
               json={"siteId": site_id, "terms": ["seo tool", "ai writer"]})
    assert r.status_code in (200, 201), r.text
    r = s.get(f"{API}/search-terms?siteId={site_id}", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()["data"]
    if items:
        tid = items[0]["id"]
        r = s.delete(f"{API}/search-terms/{tid}", headers=auth_headers)
        assert r.status_code in (200, 204)


def test_search_terms_ai_suggest(s, auth_headers, site_id):
    r = s.post(f"{API}/search-terms/ai-suggest", headers=auth_headers,
               json={"siteId": site_id, "topic": "ai marketing", "count": 3},
               timeout=60)
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["data"], (list, dict))


def test_article_settings(s, auth_headers, site_id):
    r = s.get(f"{API}/article-settings/{site_id}", headers=auth_headers)
    assert r.status_code == 200
    r = s.put(f"{API}/article-settings/{site_id}", headers=auth_headers,
              json={"tone": "friendly", "length": "medium"})
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Articles + jobs                                                              #
# --------------------------------------------------------------------------- #
def test_articles_list(s, auth_headers, site_id):
    r = s.get(f"{API}/articles?siteId={site_id}", headers=auth_headers)
    assert r.status_code == 200
    j = r.json(); _expect_shape(j); assert "pagination" in j


def test_article_generate_and_poll(s, auth_headers, site_id):
    r = s.post(f"{API}/articles/generate", headers=auth_headers,
               json={"siteId": site_id, "searchTerm": "ai seo tools"})
    assert r.status_code in (200, 201, 202), r.text
    job_id = r.json()["data"].get("jobId") or r.json()["data"].get("id")
    assert job_id
    for _ in range(15):
        time.sleep(2)
        rr = s.get(f"{API}/articles/job/{job_id}", headers=auth_headers)
        if rr.status_code == 200 and rr.json()["data"].get("status") in ("completed", "failed", "COMPLETED", "FAILED"):
            break
    assert rr.status_code == 200


def test_articles_calendar(s, auth_headers, site_id):
    r = s.get(f"{API}/articles/calendar?siteId={site_id}&year=2026&month=5",
              headers=auth_headers)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Social                                                                       #
# --------------------------------------------------------------------------- #
def test_social_accounts(s, auth_headers):
    r = s.get(f"{API}/social/accounts", headers=auth_headers)
    assert r.status_code == 200


def test_social_auth_url(s, auth_headers):
    r = s.get(f"{API}/social/auth/twitter", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["data"].get("authUrl")


def test_social_posts_list(s, auth_headers):
    r = s.get(f"{API}/social/posts", headers=auth_headers); assert r.status_code == 200


def test_social_post_generate(s, auth_headers, site_id):
    # social/posts/generate requires articleId; try with a synthetic ID –
    # accept 200/202 (queued) or 404/400 (article not found) as valid
    r = s.post(f"{API}/social/posts/generate", headers=auth_headers,
               json={"articleId": "nonexistent-id", "platforms": ["twitter"]})
    assert r.status_code in (200, 201, 202, 400, 404), r.text


# --------------------------------------------------------------------------- #
# AI Visibility                                                                #
# --------------------------------------------------------------------------- #
def test_ai_visibility_scans(s, auth_headers, site_id):
    r = s.get(f"{API}/ai-visibility/scans?siteId={site_id}", headers=auth_headers)
    assert r.status_code == 200


def test_ai_visibility_scan_enqueue(s, auth_headers, site_id):
    r = s.post(f"{API}/ai-visibility/scan", headers=auth_headers,
               json={"siteId": site_id, "queries": ["best seo tool"]})
    assert r.status_code in (200, 201, 202), r.text


def test_ai_visibility_simulate(s, auth_headers):
    r = s.post(f"{API}/ai-visibility/simulate", headers=auth_headers,
               json={"brand": "Jalwa", "query": "best seo"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# AI Writer (real OpenAI)                                                      #
# --------------------------------------------------------------------------- #
def test_brand_voice_get(s, auth_headers, site_id):
    r = s.get(f"{API}/brand-voice/{site_id}", headers=auth_headers)
    assert r.status_code == 200


def test_brand_voice_train(s, auth_headers, site_id):
    r = s.post(f"{API}/brand-voice/train", headers=auth_headers,
               json={"siteId": site_id,
                     "contentSamples": ["We build delightful products.",
                                         "Designed for makers and dreamers."]})
    assert r.status_code in (200, 201, 202), r.text


def test_content_library(s, auth_headers):
    r = s.get(f"{API}/content/library", headers=auth_headers); assert r.status_code == 200


def test_content_generate_real_llm(s, auth_headers, site_id):
    r = s.post(f"{API}/content/generate", headers=auth_headers,
               json={"siteId": site_id, "type": "BLOG_ARTICLE",
                     "topic": "ai seo trends 2026", "targetKeyword": "ai seo"},
               timeout=90)
    assert r.status_code in (200, 201), r.text
    assert r.json()["data"]


def test_voice_score(s, auth_headers, site_id):
    r = s.post(f"{API}/content/voice-score", headers=auth_headers,
               json={"siteId": site_id, "content": "Hello world."}, timeout=60)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Analytics / Growth / Team / User / Billing / Auto publish                    #
# --------------------------------------------------------------------------- #
def test_analytics_endpoints(s, auth_headers, site_id):
    for path in ("overview", "articles", "search-terms", "top-pages"):
        r = s.get(f"{API}/analytics/{path}?siteId={site_id}", headers=auth_headers)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"


def test_growth_score(s, auth_headers, site_id):
    r = s.get(f"{API}/growth-score?siteId={site_id}", headers=auth_headers)
    assert r.status_code == 200
    r = s.post(f"{API}/growth-score/calculate", headers=auth_headers,
               json={"siteId": site_id})
    assert r.status_code in (200, 201, 202)


def test_team(s, auth_headers):
    r = s.get(f"{API}/team", headers=auth_headers); assert r.status_code == 200


def test_user_profile_update(s, auth_headers):
    r = s.put(f"{API}/user/profile", headers=auth_headers, json={"name": "Renamed"})
    assert r.status_code == 200


def test_billing_plans(s, auth_headers):
    r = s.get(f"{API}/billing/plans", headers=auth_headers); assert r.status_code == 200


def test_billing_subscription(s, auth_headers):
    r = s.get(f"{API}/billing/subscription", headers=auth_headers); assert r.status_code == 200


def test_billing_webhook(s):
    r = s.post(f"{API}/billing/webhook",
               json={"event": "order_created", "data": {"id": "evt_1"}})
    assert r.status_code in (200, 202)


def test_auto_publish_connections(s, auth_headers, site_id):
    r = s.get(f"{API}/publish/connections/{site_id}", headers=auth_headers)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Plugin (X-Jalwa-API-Key)                                                     #
# --------------------------------------------------------------------------- #
def test_plugin_verify(s, auth_headers, site_id):
    # fetch site detail to read apiKey
    r = s.get(f"{API}/sites/{site_id}", headers=auth_headers)
    api_key = r.json()["data"].get("apiKey")
    assert api_key, "site should expose apiKey"
    h = {"X-Jalwa-API-Key": api_key, "Content-Type": "application/json"}
    r = s.post(f"{API}/plugin/verify", headers=h, json={})
    assert r.status_code == 200, r.text
    r = s.post(f"{API}/plugin/ping", headers=h, json={"version": "1.0.0"})
    assert r.status_code == 200, r.text
    r = s.get(f"{API}/plugin/articles/pending", headers=h)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Admin                                                                        #
# --------------------------------------------------------------------------- #
def test_admin_login_wrong():
    r = requests.post(f"{API}/admin/auth/login",
                      json={"username": "jalwa", "password": "bad"})
    assert r.status_code == 401


def test_admin_verify(s, admin_headers):
    r = s.get(f"{API}/admin/auth/verify", headers=admin_headers)
    assert r.status_code == 200


def test_admin_no_session(s):
    r = s.get(f"{API}/admin/dashboard/stats")
    assert r.status_code == 401
    assert r.json()["code"] == "ADMIN_UNAUTHORIZED"


def test_admin_dashboard(s, admin_headers):
    r = s.get(f"{API}/admin/dashboard/stats", headers=admin_headers)
    assert r.status_code == 200
    r = s.get(f"{API}/admin/dashboard/activity", headers=admin_headers)
    assert r.status_code == 200


def test_admin_users(s, admin_headers):
    r = s.get(f"{API}/admin/users", headers=admin_headers); assert r.status_code == 200
    j = r.json(); _expect_shape(j); assert "pagination" in j


def test_admin_plans_crud(s, admin_headers):
    r = s.get(f"{API}/admin/plans", headers=admin_headers); assert r.status_code == 200
    r = s.post(f"{API}/admin/plans", headers=admin_headers,
               json={"name": f"TEST_{RUN_TAG}", "monthlyPrice": 9, "annualPrice": 90,
                     "description": "test", "articlesPerMonth": 1, "socialPostsPerMonth": 1,
                     "aiScansPerMonth": 1, "teamSeats": 1, "cmsConnections": 1,
                     "brandVoiceModel": False, "competitorComparison": False,
                     "prioritySupport": False, "whiteLabel": False, "isActive": True,
                     "sortOrder": 999})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]
    r = s.put(f"{API}/admin/plans/{pid}", headers=admin_headers, json={"price": 19})
    assert r.status_code == 200
    r = s.delete(f"{API}/admin/plans/{pid}", headers=admin_headers)
    assert r.status_code in (200, 204)


def test_admin_coupons_crud(s, admin_headers):
    r = s.post(f"{API}/admin/coupons", headers=admin_headers,
               json={"code": f"T{RUN_TAG}", "type": "PERCENTAGE", "value": 10,
                     "duration": "ONCE"})
    assert r.status_code in (200, 201), r.text
    cid = r.json()["data"]["id"]
    r = s.delete(f"{API}/admin/coupons/{cid}", headers=admin_headers)
    assert r.status_code in (200, 204)


def test_admin_blog_crud(s, admin_headers):
    r = s.post(f"{API}/admin/blog", headers=admin_headers,
               json={"title": f"Hello {RUN_TAG}", "content": "Body", "status": "published"})
    assert r.status_code in (200, 201), r.text
    bid = r.json()["data"]["id"]
    slug = r.json()["data"].get("slug")
    assert slug, "auto-slug expected"
    r = s.delete(f"{API}/admin/blog/{bid}", headers=admin_headers)
    assert r.status_code in (200, 204)


def test_admin_api_keys(s, admin_headers):
    r = s.get(f"{API}/admin/api-keys", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    # data may be list of {key, value (masked)} or dict
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("value"):
                assert "*" in str(item["value"]) or len(str(item["value"])) <= 12


def test_admin_analytics(s, admin_headers):
    for p in ("users", "revenue", "modules", "funnel"):
        r = s.get(f"{API}/admin/analytics/{p}", headers=admin_headers)
        assert r.status_code == 200, f"{p}: {r.status_code} {r.text}"


def test_admin_settings(s, admin_headers):
    r = s.get(f"{API}/admin/settings", headers=admin_headers)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Rate-limit tests (LAST – they pollute in-memory state)                       #
# --------------------------------------------------------------------------- #
def test_zz_ai_visibility_demo_rate_limit():
    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"
    sess.headers["X-Forwarded-For"] = f"9.9.9.{uuid.uuid4().int % 250}"
    last = None
    for i in range(6):
        last = sess.post(f"{API}/ai-visibility/demo",
                         json={"brand": "X", "query": "best"})
    assert last.status_code == 429, last.text
    assert last.json().get("code") == "RATE_LIMITED"


def test_zz_admin_lockout():
    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"
    sess.headers["X-Forwarded-For"] = f"8.8.8.{uuid.uuid4().int % 250}"
    last = None
    for _ in range(6):
        last = sess.post(f"{API}/admin/auth/login",
                         json={"username": "jalwa", "password": "wrong"})
    assert last.status_code == 429, last.text
    assert last.json().get("code") == "LOCKED_OUT"
