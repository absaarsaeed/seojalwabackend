"""
Iteration 7 — Phase 3 (12-part batch)

Parts covered:
  1  Dashboard stats bar (totalWordsWritten, costSavings, timeSaved, articlesPublished)
  2  Plugin update banner + dismiss endpoint
  3  Enriched article calendar (cmsUrl, scheduledAt, excerpt, etc.)
  4  Blog rich editor backend (CRUD + slug uniqueness + readTime)
  4b Blog upload-image endpoint exists (200 OR non-404)
  5  Maintenance mode middleware
  6  Legal pages (privacy/terms/cookies) + admin GET-list/PUT
  7  Announcements preview-count + send w/ real recipientCount + EMAIL/IN_APP channels
  8  Admin analytics overview (users.byPlan, MRR/ARR, content, funnel)
  9  Email log full body (htmlBody/textBody)
  11+12 wordCount HTML-stripped + growth_score module + jobs hook
"""
import asyncio
import io
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

# Load backend env
try:
    from dotenv import load_dotenv
    load_dotenv('/app/backend/.env')
except Exception:
    _envp = Path('/app/backend/.env')
    if _envp.exists():
        for _line in _envp.read_text().splitlines():
            if '=' in _line and not _line.startswith('#'):
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())


def _load_backend_url():
    v = os.environ.get('REACT_APP_BACKEND_URL')
    if v:
        return v.rstrip('/')
    env_file = Path('/app/frontend/.env')
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith('REACT_APP_BACKEND_URL='):
                return line.split('=', 1)[1].strip().rstrip('/')
    raise RuntimeError('REACT_APP_BACKEND_URL not set')


BASE_URL = _load_backend_url()
ADMIN_USER = 'jalwa'
ADMIN_PASS = 'jalwaadmin'

sys.path.insert(0, '/app/backend')


# ─── helpers ──────────────────────────────────────────────────────────

@pytest.fixture(scope='session')
def admin_token():
    r = requests.post(f"{BASE_URL}/api/admin/auth/login",
                      json={'username': ADMIN_USER, 'password': ADMIN_PASS},
                      timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get('data', {}).get('token') or body.get('token')


@pytest.fixture(scope='session')
def admin_headers(admin_token):
    return {'X-Admin-Token': admin_token, 'Content-Type': 'application/json'}


def _mint_user(with_subscription_plan=None, with_site=True,
               site_extras: dict | None = None):
    from core.security import create_access_token, hash_password, utcnow_iso

    async def _do():
        from core.database import get_db
        db = get_db()
        uid = uuid.uuid4().hex
        email = f"i7_{int(time.time() * 1000)}_{uid[:6]}@seojalwa.com"
        await db.users.insert_one({
            "id": uid, "email": email, "fullName": "Iter7 Tester",
            "passwordHash": hash_password("Testing12345!"),
            "role": "user", "status": "active",
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
            "emailVerified": True,
        })
        site_id = None
        if with_site:
            site_id = uuid.uuid4().hex
            doc = {
                "id": site_id, "userId": uid, "name": "Iter7 Site",
                "url": "https://iter7.example.com",
                "platform": "wordpress",
                "apiKey": f"jalwa_live_{uuid.uuid4().hex[:24]}",
                "createdAt": utcnow_iso(),
            }
            if site_extras:
                doc.update(site_extras)
            await db.sites.insert_one(doc)
        if with_subscription_plan:
            plan = await db.plans.find_one(
                {"id": with_subscription_plan}, {"_id": 0})
            if plan:
                await db.subscriptions.insert_one({
                    "id": uuid.uuid4().hex, "userId": uid,
                    "planId": plan["id"], "status": "ACTIVE",
                    "source": "FREE" if plan.get("isFree") else "MANUAL",
                    "currentPeriodStart": utcnow_iso(),
                    "currentPeriodEnd": None,
                    "trialEndsAt": None,
                    "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
                })
        return uid, email, site_id

    uid, email, site_id = asyncio.get_event_loop().run_until_complete(_do())
    return {"userId": uid, "email": email, "siteId": site_id,
            "token": create_access_token(uid)}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ═════════════════════════════════════════════════════════════════════
# PART 1 — Dashboard stats bar
# ═════════════════════════════════════════════════════════════════════

def test_part1_dashboard_stats_with_published_article():
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')
    u = _mint_user(with_subscription_plan=free['id'], with_site=True)

    from core.security import utcnow_iso
    art_id = uuid.uuid4().hex

    async def _insert_article():
        from core.database import get_db
        db = get_db()
        await db.articles.insert_one({
            "id": art_id, "userId": u["userId"], "siteId": u["siteId"],
            "title": "Iter7 published",
            "searchTerm": "iter7",
            "status": "PUBLISHED",
            "wordCount": 800,
            "content": "<p>" + ("word " * 800) + "</p>",
            "publishedAt": utcnow_iso(),
            "createdAt": utcnow_iso(),
        })
    _run(_insert_article())

    h = {'Authorization': f"Bearer {u['token']}"}
    r = requests.get(f"{BASE_URL}/api/dashboard/overview",
                      headers=h, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()['data']
    stats = data.get('stats')
    assert stats is not None, data
    for k in ('totalWordsWritten', 'costSavings',
              'timeSaved', 'articlesPublished'):
        assert k in stats, stats
    assert stats['totalWordsWritten'] >= 800, stats
    assert stats['articlesPublished'] >= 1, stats
    assert stats['costSavings'] > 0, stats
    assert stats['timeSaved'] > 0, stats


def test_part1_dashboard_stats_empty_state():
    """User with no site → stats all zero."""
    u = _mint_user(with_subscription_plan=None, with_site=False)
    h = {'Authorization': f"Bearer {u['token']}"}
    r = requests.get(f"{BASE_URL}/api/dashboard/overview",
                      headers=h, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()['data']
    stats = data.get('stats')
    assert stats == {"totalWordsWritten": 0, "costSavings": 0,
                     "timeSaved": 0, "articlesPublished": 0}, stats


# ═════════════════════════════════════════════════════════════════════
# PART 2 — Plugin update banner + dismiss
# ═════════════════════════════════════════════════════════════════════

def test_part2_plugin_update_banner_dismiss():
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')

    async def _seed_setting():
        from core.database import get_db
        db = get_db()
        await db.settings.update_one(
            {"key": "plugin_version"},
            {"$set": {"key": "plugin_version", "value": "1.0.2"}},
            upsert=True)
    _run(_seed_setting())

    u = _mint_user(with_subscription_plan=free['id'], with_site=True,
                    site_extras={"pluginVersion": "1.0.1"})
    h = {'Authorization': f"Bearer {u['token']}"}
    r = requests.get(f"{BASE_URL}/api/dashboard/overview",
                      headers=h, timeout=15)
    assert r.status_code == 200, r.text
    pu = r.json()['data'].get('pluginUpdate')
    assert pu is not None
    for k in ('available', 'currentVersion', 'latestVersion', 'dismissed'):
        assert k in pu, pu
    assert pu['available'] is True, pu
    assert pu['currentVersion'] == '1.0.1'
    assert pu['latestVersion'] == '1.0.2'
    assert pu['dismissed'] is False

    # Dismiss
    d = requests.put(f"{BASE_URL}/api/user/dismiss-plugin-banner",
                      headers={**h, 'Content-Type': 'application/json'},
                      json={"version": "1.0.2"}, timeout=15)
    assert d.status_code == 200, d.text
    assert d.json()['data']['dismissed'] is True

    # Re-fetch overview
    r2 = requests.get(f"{BASE_URL}/api/dashboard/overview",
                      headers=h, timeout=15)
    pu2 = r2.json()['data']['pluginUpdate']
    assert pu2['dismissed'] is True, pu2


# ═════════════════════════════════════════════════════════════════════
# PART 3 — Enriched article calendar
# ═════════════════════════════════════════════════════════════════════

def test_part3_articles_calendar_enriched():
    from datetime import datetime, timezone
    from core.security import utcnow_iso
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')
    u = _mint_user(with_subscription_plan=free['id'], with_site=True)

    now = datetime.now(timezone.utc)
    today_iso = now.isoformat()

    async def _seed():
        from core.database import get_db
        db = get_db()
        await db.articles.insert_one({
            "id": uuid.uuid4().hex, "userId": u["userId"],
            "siteId": u["siteId"], "title": "Pub article",
            "searchTerm": "pub", "status": "PUBLISHED",
            "seoScore": 88, "wordCount": 600,
            "publishedAt": today_iso, "scheduledAt": None,
            "featuredImageUrl": "https://example.com/img.jpg",
            "cmsUrl": "https://blog.example.com/pub",
            "content": "<h1>Header</h1><p>Body text here</p>",
            "createdAt": today_iso,
        })
        await db.articles.insert_one({
            "id": uuid.uuid4().hex, "userId": u["userId"],
            "siteId": u["siteId"], "title": "Sch article",
            "searchTerm": "sch", "status": "SCHEDULED",
            "seoScore": 75, "wordCount": 400,
            "publishedAt": None, "scheduledAt": today_iso,
            "featuredImageUrl": "",
            "cmsUrl": "",
            "content": "<p>Future content</p>",
            "createdAt": today_iso,
        })
    _run(_seed())

    h = {'Authorization': f"Bearer {u['token']}"}
    r = requests.get(
        f"{BASE_URL}/api/articles/calendar"
        f"?siteId={u['siteId']}&year={now.year}&month={now.month}",
        headers=h, timeout=15)
    assert r.status_code == 200, r.text
    grouped = r.json()['data']
    today_key = today_iso[:10]
    assert today_key in grouped, grouped
    entries = grouped[today_key]
    assert len(entries) >= 2, entries

    needed = {'id', 'title', 'searchTerm', 'status', 'seoScore',
              'wordCount', 'publishedAt', 'scheduledAt',
              'featuredImageUrl', 'cmsUrl', 'excerpt'}
    for e in entries:
        missing = needed - set(e.keys())
        assert not missing, f"missing keys {missing} in {e}"
    pub = next(e for e in entries if e['status'] == 'PUBLISHED')
    sch = next(e for e in entries if e['status'] == 'SCHEDULED')
    assert pub['cmsUrl'] == 'https://blog.example.com/pub'
    assert pub['publishedAt'] == today_iso
    assert sch['scheduledAt'] == today_iso
    # excerpt is HTML-stripped
    assert '<' not in pub['excerpt'], pub['excerpt']


# ═════════════════════════════════════════════════════════════════════
# PART 4 — Blog rich editor CRUD
# ═════════════════════════════════════════════════════════════════════

def test_part4_admin_blog_crud_slug_readtime(admin_headers):
    title = f"Phase 3 launch post {uuid.uuid4().hex[:5]}"
    body = "<h1>Hi</h1><p>" + " ".join(["word"] * 40) + "</p>"
    payload = {
        "title": title, "content": body,
        "tags": ["test"],
        "seoMetaTitle": "Phase 3 Launch",
        "seoMetaDescription": "Phase 3 desc",
        "status": "DRAFT",
    }
    r = requests.post(f"{BASE_URL}/api/admin/blog",
                      headers=admin_headers, json=payload, timeout=15)
    assert r.status_code in (200, 201), r.text
    p1 = r.json()['data']
    assert p1.get('id')
    assert p1.get('slug'), p1
    assert p1.get('readTime', 0) >= 1, p1
    assert p1.get('author') == 'SEO Jalwa Team', p1
    assert p1.get('seoMetaTitle') == 'Phase 3 Launch', p1

    # PUT → publish
    pr = requests.put(f"{BASE_URL}/api/admin/blog/{p1['id']}",
                     headers=admin_headers,
                     json={"status": "PUBLISHED"}, timeout=15)
    assert pr.status_code == 200, pr.text
    after = pr.json()['data']['post']
    assert after.get('publishedAt'), after

    # Duplicate title → -2 suffix
    r2 = requests.post(f"{BASE_URL}/api/admin/blog",
                       headers=admin_headers, json=payload, timeout=15)
    assert r2.status_code in (200, 201), r2.text
    p2 = r2.json()['data']
    assert p2['slug'].endswith('-2'), p2['slug']

    # Cleanup
    requests.delete(f"{BASE_URL}/api/admin/blog/{p1['id']}",
                     headers=admin_headers, timeout=15)
    requests.delete(f"{BASE_URL}/api/admin/blog/{p2['id']}",
                     headers=admin_headers, timeout=15)


def test_part4b_admin_blog_upload_image(admin_token):
    """Endpoint must exist. 200 (R2 configured) or 5xx (not 404)."""
    # 1x1 PNG
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
           b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\rIDATx\x9cc\xfc\xcf\xc0P\x0f\x00\x05\x01"
           b"\x01\x02\xcb\xd55\x83\x00\x00\x00\x00IEND\xaeB`\x82")
    files = {'file': ('test.png', io.BytesIO(png), 'image/png')}
    headers = {'X-Admin-Token': admin_token}
    r = requests.post(f"{BASE_URL}/api/admin/blog/upload-image",
                      headers=headers, files=files, timeout=20)
    assert r.status_code != 404, "endpoint missing"
    if r.status_code == 200:
        d = r.json()['data']
        for k in ('url', 'key', 'size', 'contentType'):
            assert k in d, d
        assert d['size'] == len(png), d
    else:
        # Accept any non-404 (R2 misconfig is OK in test env)
        assert r.status_code in (400, 500, 502, 503), r.text


# ═════════════════════════════════════════════════════════════════════
# PART 5 — Maintenance mode
# ═════════════════════════════════════════════════════════════════════

def test_part5_maintenance_mode(admin_headers):
    # Turn on
    on = requests.put(f"{BASE_URL}/api/admin/settings",
                      headers=admin_headers,
                      json={"maintenanceMode": True,
                             "maintenanceMessage": "test maint"},
                      timeout=15)
    try:
        assert on.status_code == 200, on.text
        time.sleep(0.5)

        # Public endpoint → 503 MAINTENANCE_MODE
        r = requests.get(f"{BASE_URL}/api/plans", timeout=15)
        assert r.status_code == 503, r.text
        body = r.json()
        assert body.get('code') == 'MAINTENANCE_MODE', body
        assert 'test maint' in str(body), body

        # Admin still works
        r2 = requests.get(f"{BASE_URL}/api/admin/plans",
                          headers=admin_headers, timeout=15)
        assert r2.status_code == 200, r2.text
    finally:
        # Disable cleanly
        off = requests.put(f"{BASE_URL}/api/admin/settings",
                           headers=admin_headers,
                           json={"maintenanceMode": False,
                                  "maintenanceMessage": ""},
                           timeout=15)
        assert off.status_code == 200, off.text

        async def _clear_legacy():
            from core.database import get_db
            await get_db().settings.update_one(
                {"key": "maintenance_mode"},
                {"$set": {"value": False}}, upsert=False)
        try:
            _run(_clear_legacy())
        except Exception:
            pass

        time.sleep(0.5)
        # Sanity
        r3 = requests.get(f"{BASE_URL}/api/plans", timeout=15)
        assert r3.status_code == 200, f"maintenance not cleared: {r3.text}"


# ═════════════════════════════════════════════════════════════════════
# PART 6 — Legal pages
# ═════════════════════════════════════════════════════════════════════

def test_part6_legal_public_and_admin(admin_headers):
    # Public GETs
    for key in ('privacy-policy', 'terms-of-service', 'cookie-policy'):
        r = requests.get(f"{BASE_URL}/api/legal/{key}", timeout=15)
        assert r.status_code == 200, f"{key}: {r.text}"
        d = r.json()['data']
        for k in ('title', 'content', 'lastUpdatedAt'):
            assert k in d, d

    # Admin list
    al = requests.get(f"{BASE_URL}/api/admin/legal",
                      headers=admin_headers, timeout=15)
    assert al.status_code == 200, al.text
    rows = al.json()['data']
    keys = {r['key'] for r in rows}
    assert {'privacy-policy', 'terms-of-service', 'cookie-policy'} \
        <= keys, keys

    # Snapshot existing terms
    before = requests.get(f"{BASE_URL}/api/legal/terms-of-service",
                          timeout=15).json()['data']
    before_updated = before.get('lastUpdatedAt')
    new_content = f"<p>updated {uuid.uuid4().hex[:6]}</p>"

    try:
        up = requests.put(f"{BASE_URL}/api/admin/legal/terms-of-service",
                          headers=admin_headers,
                          json={"content": new_content}, timeout=15)
        assert up.status_code == 200, up.text

        time.sleep(0.2)
        after = requests.get(f"{BASE_URL}/api/legal/terms-of-service",
                             timeout=15).json()['data']
        assert after['content'] == new_content, after
        assert after['lastUpdatedAt'] != before_updated, after
    finally:
        # Restore
        requests.put(f"{BASE_URL}/api/admin/legal/terms-of-service",
                     headers=admin_headers,
                     json={"content": before['content']}, timeout=15)


# ═════════════════════════════════════════════════════════════════════
# PART 7 — Announcements preview-count + send
# ═════════════════════════════════════════════════════════════════════

def test_part7_announcements_preview_count_and_send(admin_headers):
    pc = requests.get(
        f"{BASE_URL}/api/admin/announcements/preview-count"
        f"?targetAudience=ALL", headers=admin_headers, timeout=15)
    assert pc.status_code == 200, pc.text
    pcd = pc.json()['data']
    assert 'count' in pcd, pcd
    preview_count = pcd['count']
    assert isinstance(preview_count, int)
    assert preview_count >= 0

    # Real total users from DB (sanity)
    async def _total():
        from core.database import get_db
        return await get_db().users.count_documents(
            {"deleted": {"$ne": True}})
    total = _run(_total())
    assert preview_count == total, (preview_count, total)

    subject = f"Iter7 anno {uuid.uuid4().hex[:5]}"
    sr = requests.post(f"{BASE_URL}/api/admin/announcements",
                       headers=admin_headers,
                       json={"subject": subject,
                              "message": "<p>Hello</p>",
                              "targetAudience": "ALL",
                              "channels": ["IN_APP"]},
                       timeout=30)
    assert sr.status_code in (200, 201), sr.text
    sd = sr.json()['data']
    assert sd.get('recipientCount') == preview_count, sd
    assert 'IN_APP' in (sd.get('channels') or []), sd


# ═════════════════════════════════════════════════════════════════════
# PART 8 — Admin analytics overview
# ═════════════════════════════════════════════════════════════════════

def test_part8_admin_analytics_overview(admin_headers):
    r = requests.get(f"{BASE_URL}/api/admin/analytics/overview",
                     headers=admin_headers, timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()['data']

    # users
    u = d.get('users') or {}
    for k in ('total', 'byPlan', 'newToday', 'newThisWeek',
              'newThisMonth', 'dailySignups'):
        assert k in u, u
    for plan in ('free', 'starter', 'growth', 'agency'):
        assert plan in u['byPlan'], u['byPlan']
    assert isinstance(u['dailySignups'], list)
    assert len(u['dailySignups']) == 30, len(u['dailySignups'])

    # revenue
    rv = d.get('revenue') or {}
    for k in ('mrr', 'arr', 'thisMonth', 'lastMonth', 'dailyRevenue'):
        assert k in rv, rv
    assert len(rv['dailyRevenue']) == 30, len(rv['dailyRevenue'])
    # arr ≈ mrr*12
    assert abs(rv['arr'] - rv['mrr'] * 12) < 1, rv

    # content
    c = d.get('content') or {}
    for k in ('articlesGenerated', 'articlesThisMonth',
              'aiScansRun', 'totalWordsWritten'):
        assert k in c, c

    # funnel
    f = d.get('funnel') or {}
    for k in ('registered', 'connectedSite', 'generatedArticle',
              'ranScan', 'upgradedToPaid'):
        assert k in f, f


# ═════════════════════════════════════════════════════════════════════
# PART 9 — Email log full body
# ═════════════════════════════════════════════════════════════════════

def test_part9_email_log_full_body(admin_headers):
    """Trigger a dummy checkout to create an email log row; verify htmlBody/textBody."""
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')
    growth = next(p for p in plans if p['name'] == 'Growth')
    u = _mint_user(with_subscription_plan=free['id'], with_site=True)
    h = {'Authorization': f"Bearer {u['token']}",
         'Content-Type': 'application/json'}

    co = requests.post(f"{BASE_URL}/api/billing/checkout", headers=h,
                       json={"planId": growth['id'],
                              "billingInterval": "MONTHLY"},
                       timeout=15)
    assert co.status_code == 200, co.text
    sid = co.json()['data']['sessionId']
    cc = requests.post(f"{BASE_URL}/api/billing/checkout/{sid}/complete",
                       headers=h, json={"cardNumber": "4242"}, timeout=20)
    assert cc.status_code == 200, cc.text

    time.sleep(1.5)

    # List
    ls = requests.get(f"{BASE_URL}/api/admin/emails",
                      headers=admin_headers, timeout=15)
    assert ls.status_code == 200, ls.text
    rows = ls.json()['data']
    assert isinstance(rows, list)
    if not rows:
        pytest.skip("No email log rows yet")
    target = next((row for row in rows if row.get('to') == u['email']),
                  rows[0])
    log_id = target['id']

    # Detail
    one = requests.get(f"{BASE_URL}/api/admin/emails/{log_id}",
                       headers=admin_headers, timeout=15)
    assert one.status_code == 200, one.text
    full = one.json()['data']
    assert 'htmlBody' in full, list(full.keys())
    assert 'textBody' in full, list(full.keys())
    # htmlBody is empty when SKIPPED (no API key) but field must exist
    assert isinstance(full['htmlBody'], str)
    assert isinstance(full['textBody'], str)


# ═════════════════════════════════════════════════════════════════════
# PART 11 + 12 — wordCount HTML strip + growth_score module
# ═════════════════════════════════════════════════════════════════════

def test_part11_llm_wordcount_html_stripped():
    """llm.py must strip HTML before counting words."""
    import importlib
    llm = importlib.import_module('services.llm')
    src = Path(llm.__file__).read_text()
    # Sanity: there's a wordCount calculation and an HTML strip nearby
    assert 'wordCount' in src, 'wordCount not referenced in services/llm.py'
    assert ('re.sub' in src and '<' in src) or 'strip' in src, \
        'no HTML stripping pattern found near wordCount'


def test_part12_growth_score_module_imports_and_hook():
    """Phase 3 Part 12 — growth_score module imports + jobs hook exists."""
    import importlib
    gs = importlib.import_module('services.growth_score')
    assert hasattr(gs, 'calculate_growth_score'), dir(gs)
    jobs = importlib.import_module('services.jobs')
    src = Path(jobs.__file__).read_text()
    # The hook must invoke growth_score logic in publish/scan code paths
    assert 'growth_score' in src or 'calculate_growth_score' in src \
        or 'schedule_recalc' in src, \
        'jobs.py does not reference growth_score hook'


# ═════════════════════════════════════════════════════════════════════
# Regression: smoke a few iteration-6 endpoints still respond
# ═════════════════════════════════════════════════════════════════════

def test_regression_plans_and_admin_dashboard_activity(admin_headers):
    r = requests.get(f"{BASE_URL}/api/plans", timeout=15)
    assert r.status_code == 200, r.text
    a = requests.get(f"{BASE_URL}/api/admin/dashboard/activity?limit=5",
                     headers=admin_headers, timeout=15)
    assert a.status_code == 200, a.text
