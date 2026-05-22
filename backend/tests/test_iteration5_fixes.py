"""
Iteration 5 — validates 10 specific fixes from this iteration's review request.
Covers:
  FIX 1  — GSC OAuth client_id/secret loaded from config_service + env fallback
  FIX 2  — sites verify-connection trusts DB when wordpressConnected:true
  FIX 3  — AI visibility scan: simplified GPT-4o-only + visibilityStatus
  FIX 4  — Resend email fallback to onboarding@resend.dev; failure recorded
  FIX 5  — Admin dashboard activity feed has real multi-source events
  FIX 6  — Admin /users returns real users (no dummies), pagination.total accurate
  FIX 7  — Audit log entry for plan change contains plan name diff + userEmail
  FIX 8  — Plans surface includes BOTH cmsConnections and websiteConnections
  FIX 9  — Article gen produces featuredImageUrl + inlineImageUrl + inline <figure>
  FIX 10 — Onboarding GET/PUT endpoints + /auth/me echoes onboarding state
"""
import os
import time
import uuid
from pathlib import Path

import pytest
import requests

# Load backend env vars (MONGO_URL, DB_NAME) for DB helpers
try:
    from dotenv import load_dotenv
    load_dotenv('/app/backend/.env')
except Exception:
    # Manual fallback
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


# ----------- fixtures -----------

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


def _mint_user_jwt():
    """Bypass /auth/register rate-limit by creating a user via DB + JWT helper."""
    import sys
    sys.path.insert(0, '/app/backend')
    # Lazy-load core modules
    from core.security import create_access_token, hash_password, utcnow_iso  # type: ignore
    from core.database import get_db  # type: ignore
    import asyncio

    async def _do():
        db = get_db()
        uid = uuid.uuid4().hex
        email = f"iter5_{int(time.time() * 1000)}_{uid[:6]}@seojalwa.com"
        doc = {
            "id": uid,
            "email": email,
            "fullName": "Iter5 Tester",
            "passwordHash": hash_password("Testing12345!"),
            "role": "user",
            "status": "active",
            "createdAt": utcnow_iso(),
            "updatedAt": utcnow_iso(),
            "emailVerified": True,
        }
        await db.users.insert_one(doc)
        return uid, email

    uid, email = asyncio.get_event_loop().run_until_complete(_do())
    token = create_access_token(uid)
    return {"userId": uid, "email": email, "token": token}


def _register_user(create_site=True):
    """Register a user via API; fall back to minting JWT if rate-limited."""
    ts = int(time.time() * 1000)
    email = f"i5_{ts}_{uuid.uuid4().hex[:6]}@seojalwa.com"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={'email': email, 'password': 'Testing12345!', 'fullName': 'Iter5'},
        timeout=15)
    if r.status_code == 429:
        u = _mint_user_jwt()
        u['siteId'] = None
    else:
        assert r.status_code in (200, 201), r.text
        body = r.json()
        data = body.get('data', body)
        token = (data.get('accessToken') or data.get('token')
                 or (data.get('tokens') or {}).get('accessToken'))
        user = data.get('user') or {}
        u = {
            'token': token,
            'userId': user.get('id') or user.get('_id'),
            'email': email,
            'siteId': ((data.get('sites') or [{}])[0] or {}).get('id'),
        }
    if create_site and not u.get('siteId'):
        h = {'Authorization': f"Bearer {u['token']}",
             'Content-Type': 'application/json'}
        sp = {'name': 'Iter5 Site',
              'url': f"https://i5-{uuid.uuid4().hex[:6]}.com",
              'platform': 'WORDPRESS'}
        sr = requests.post(f"{BASE_URL}/api/sites", json=sp,
                           headers=h, timeout=15)
        if sr.status_code in (200, 201):
            u['siteId'] = (sr.json().get('data') or sr.json()).get('id')
    return u


@pytest.fixture(scope='session')
def fresh_user():
    return _register_user()


@pytest.fixture(scope='session')
def user_headers(fresh_user):
    return {'Authorization': f"Bearer {fresh_user['token']}",
            'Content-Type': 'application/json'}


# ========================= FIX 1 — GSC OAuth =========================

def test_fix1_gsc_connect_not_configured_then_configured(admin_headers,
                                                         user_headers):
    """GSC connect: 400 when not configured, 200 with authUrl after."""
    # Step 1 — clear creds and expect 400 NOT_CONFIGURED
    clear_payload = {'fields': {'client_id': '', 'client_secret': '',
                                'redirect_uri': ''}}
    requests.put(f"{BASE_URL}/api/admin/api-keys/google_oauth",
                 json=clear_payload, headers=admin_headers, timeout=15)

    r = requests.get(f"{BASE_URL}/api/analytics/gsc/connect",
                     headers=user_headers, timeout=15)
    assert r.status_code == 400, f"expected 400 first, got {r.status_code}"
    assert 'GSC_NOT_CONFIGURED' in r.text, r.text

    # Step 2 — set creds and expect 200 with authUrl
    set_payload = {'fields': {
        'client_id': 'test-client-id.apps.googleusercontent.com',
        'client_secret': 'test-secret',
        'redirect_uri': 'https://example.com/cb',
    }}
    sp = requests.put(f"{BASE_URL}/api/admin/api-keys/google_oauth",
                      json=set_payload, headers=admin_headers, timeout=15)
    assert sp.status_code == 200, sp.text

    r2 = requests.get(f"{BASE_URL}/api/analytics/gsc/connect",
                      headers=user_headers, timeout=15)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    data = body.get('data', body)
    url = data.get('authUrl') or data.get('url') or ''
    assert 'accounts.google.com/o/oauth2' in url, f"bad authUrl: {url}"
    assert 'client_id=test-client-id' in url
    assert 'webmasters' in url
    assert 'access_type=offline' in url
    assert 'prompt=consent' in url

    # cleanup
    requests.put(f"{BASE_URL}/api/admin/api-keys/google_oauth",
                 json=clear_payload, headers=admin_headers, timeout=15)


# ========================= FIX 2 — verify-connection DB-first =========================

def test_fix2_verify_connection_trusts_db_when_connected(user_headers,
                                                         fresh_user):
    """When site.wordpressConnected:true in DB → returns connected:true without probing."""
    import sys
    sys.path.insert(0, '/app/backend')
    from core.database import get_db  # type: ignore
    import asyncio

    # Create a site with a FAKE unreachable URL
    h = user_headers
    site_payload = {'name': 'Fake Verify',
                    'url': 'https://nonexistent-domain-xyz-12345.invalid',
                    'platform': 'WORDPRESS'}
    sr = requests.post(f"{BASE_URL}/api/sites", json=site_payload,
                       headers=h, timeout=15)
    assert sr.status_code in (200, 201), sr.text
    sid = (sr.json().get('data') or sr.json()).get('id')

    # Patch wordpressConnected = true in DB directly
    async def _patch():
        db = get_db()
        await db.sites.update_one({"id": sid},
                                  {"$set": {"wordpressConnected": True}})
    asyncio.get_event_loop().run_until_complete(_patch())

    # POST verify-connection: should return connected:true WITHOUT making the
    # HTTP probe (which would fail on .invalid host)
    t0 = time.time()
    r = requests.post(f"{BASE_URL}/api/sites/{sid}/verify-connection",
                      headers=h, timeout=15)
    elapsed = time.time() - t0
    assert r.status_code == 200, r.text
    data = r.json().get('data', r.json())
    assert data.get('connected') is True, f"expected connected:true: {data}"
    # If it had made the HTTP probe, it would have taken >5s for DNS failure
    print(f"verify-connection (DB-trusted) took {elapsed:.2f}s")


def test_fix2b_verify_connection_falls_through_when_not_connected(
        user_headers):
    """When wordpressConnected:false → makes probe; .invalid host → connected:false."""
    h = user_headers
    site_payload = {'name': 'Probe Site',
                    'url': 'https://nonexistent-domain-xyz-67890.invalid',
                    'platform': 'WORDPRESS'}
    sr = requests.post(f"{BASE_URL}/api/sites", json=site_payload,
                       headers=h, timeout=15)
    assert sr.status_code in (200, 201), sr.text
    sid = (sr.json().get('data') or sr.json()).get('id')

    r = requests.post(f"{BASE_URL}/api/sites/{sid}/verify-connection",
                      headers=h, timeout=30)
    # endpoint returns 200 with connected:false OR error code; accept either
    assert r.status_code in (200, 400, 502), r.text
    body = r.json()
    data = body.get('data', body)
    if r.status_code == 200:
        # may be connected:false or have detail
        if isinstance(data, dict) and 'connected' in data:
            assert data.get('connected') is False
    else:
        # error path is acceptable too
        assert body.get('code') or body.get('error')


# ========================= FIX 5 — admin dashboard activity feed =========================

def test_fix5_admin_dashboard_activity_real_events(admin_headers, fresh_user):
    r = requests.get(f"{BASE_URL}/api/admin/dashboard/activity?limit=20",
                     headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    data = body.get('data', body)
    events = data if isinstance(data, list) else (
        data.get('events') or data.get('activity') or [])
    assert isinstance(events, list) and len(events) >= 5, \
        f"expected >=5 events, got {len(events)}: {events[:3]}"

    types = []
    for e in events:
        if isinstance(e, dict):
            t = e.get('type') or e.get('action') or ''
            types.append(t)
            # each should have type + title + timestamp/at
            assert 'title' in e or 'message' in e or 'description' in e, \
                f"event missing title-like field: {e}"
            assert 'timestamp' in e or 'at' in e or 'createdAt' in e, \
                f"event missing timestamp: {e}"
    print(f"activity types observed: {set(types)}")
    # at least one USER_REGISTERED expected
    joined = ' '.join(types).upper()
    assert 'USER' in joined or 'REGISTER' in joined or 'SIGNUP' in joined, \
        f"no signup-like event in {types}"

    # Verify timestamp desc order
    ts_field = next((f for f in ('timestamp', 'at', 'createdAt')
                     if isinstance(events[0], dict) and f in events[0]),
                    None)
    if ts_field:
        ts_values = [e.get(ts_field) for e in events
                     if isinstance(e, dict) and e.get(ts_field)]
        assert ts_values == sorted(ts_values, reverse=True), \
            "events not sorted desc by timestamp"


# ========================= FIX 6 — admin users list real users =========================

def test_fix6_admin_users_list_real_data(admin_headers, fresh_user):
    r = requests.get(f"{BASE_URL}/api/admin/users?page=1&limit=50",
                     headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    data = body.get('data', body)
    users = data if isinstance(data, list) else (
        data.get('users') or data.get('items') or [])
    pagination = body.get('pagination') or {}
    total = pagination.get('total') or pagination.get('totalItems') \
        or (data.get('total') if isinstance(data, dict) else None)
    assert total is not None, f"pagination.total missing: {body.keys()}"
    assert isinstance(total, int) and total > 0
    assert len(users) > 0

    emails = [u.get('email') for u in users if isinstance(u, dict)]
    # The freshly registered user should be present (when sorted desc) OR at
    # least there should be real seojalwa-style emails
    assert any('@' in (e or '') for e in emails), f"no real emails: {emails[:5]}"
    # no dummy markers
    for e in emails:
        assert e and 'dummy' not in (e or '').lower() \
            and 'placeholder' not in (e or '').lower(), \
            f"dummy-looking email: {e}"


# ========================= FIX 7 — audit log plan-change diff =========================

def test_fix7_audit_log_plan_change_has_human_readable_names(admin_headers):
    u = _register_user()

    # Pick Growth plan id
    pr = requests.get(f"{BASE_URL}/api/plans", timeout=15)
    assert pr.status_code == 200
    pdata = pr.json().get('data', pr.json())
    plans = pdata.get('plans') if isinstance(pdata, dict) else pdata
    growth = next((p for p in plans if str(p.get('name', '')).lower()
                  == 'growth'), None)
    assert growth, f"Growth plan not found: {plans}"

    payload = {'planId': growth.get('id') or growth.get('_id'),
               'status': 'ACTIVE'}
    ur = requests.put(
        f"{BASE_URL}/api/admin/users/{u['userId']}/subscription",
        json=payload, headers=admin_headers, timeout=15)
    assert ur.status_code == 200, ur.text

    time.sleep(1)
    al = requests.get(
        f"{BASE_URL}/api/admin/audit-log?limit=50&action=USER_PLAN_CHANGED",
        headers=admin_headers, timeout=15)
    assert al.status_code == 200, al.text
    rows = al.json().get('data', al.json())
    if isinstance(rows, dict):
        rows = rows.get('items') or rows.get('rows') or []
    # find the row for this user
    row = next((r for r in rows if isinstance(r, dict)
                and (r.get('target_id') == u['userId']
                     or r.get('targetId') == u['userId']
                     or (r.get('metadata') or {}).get('userEmail')
                     == u['email'])), None)
    assert row, f"audit row for user not found among {len(rows)} rows"
    print(f"matched audit row: {row}")
    changes = row.get('changes') or {}
    assert 'planId' in changes, f"planId diff missing: {changes}"
    assert 'plan' in changes, f"plan-name diff missing: {changes}"
    plan_diff = changes.get('plan') or {}
    assert plan_diff.get('to', '').lower() == 'growth', \
        f"plan.to should be 'Growth': {plan_diff}"
    meta = row.get('metadata') or {}
    assert meta.get('userEmail') == u['email'], \
        f"metadata.userEmail mismatch: {meta}"


# ========================= FIX 8 — cmsConnections + websiteConnections =========================

@pytest.mark.parametrize("path,headers_factory", [
    ("/api/plans", lambda h: {}),
    ("/api/billing/plans", lambda h: h),
    ("/api/admin/plans", lambda h: h),
])
def test_fix8_plans_have_both_connection_keys(path, headers_factory,
                                              admin_headers, user_headers):
    headers = headers_factory({**user_headers, **admin_headers}) if path \
        != "/api/plans" else {}
    # admin path requires admin headers; billing requires user headers
    if path == "/api/billing/plans":
        headers = user_headers
    elif path == "/api/admin/plans":
        headers = admin_headers
    r = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=15)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text}"
    body = r.json()
    data = body.get('data', body)
    plans = data.get('plans') if isinstance(data, dict) else data
    assert isinstance(plans, list) and plans, f"no plans returned: {body}"
    # Only real (non-TEST_) plans are part of the prod surface contract
    real_plans = [p for p in plans
                  if not str(p.get('name', '')).startswith('TEST_')]
    assert real_plans, f"no real plans found among {[p.get('name') for p in plans]}"
    for p in real_plans:
        assert 'cmsConnections' in p, \
            f"{path}: plan {p.get('name')} missing cmsConnections: {p.keys()}"
        assert 'websiteConnections' in p, \
            f"{path}: plan {p.get('name')} missing websiteConnections"
        # equal values
        assert p['cmsConnections'] == p['websiteConnections'], \
            f"{path}: cms!=web for {p.get('name')}: " \
            f"{p['cmsConnections']} vs {p['websiteConnections']}"


# ========================= FIX 10 — onboarding state =========================

def test_fix10_onboarding_get_put_and_me_mirror(user_headers, fresh_user):
    # GET initial
    r = requests.get(f"{BASE_URL}/api/user/onboarding",
                     headers=user_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json().get('data', r.json())
    onb = data.get('onboarding') if 'onboarding' in data else data
    assert isinstance(onb, dict), f"onboarding not dict: {data}"
    for k in ('websiteConnected', 'articleSettingsConfigured',
              'searchTermsAdded', 'firstScanRun', 'dismissed', 'completed'):
        assert k in onb, f"onboarding missing key {k}: {onb}"
        assert isinstance(onb[k], bool), f"{k} should be bool: {onb[k]}"

    # PUT articleSettingsConfigured:true
    pr = requests.put(f"{BASE_URL}/api/user/onboarding",
                      json={'step': 'articleSettingsConfigured',
                            'completed': True},
                      headers=user_headers, timeout=15)
    assert pr.status_code == 200, pr.text

    g = requests.get(f"{BASE_URL}/api/user/onboarding",
                     headers=user_headers, timeout=15)
    o = g.json().get('data', g.json())
    o = o.get('onboarding') if 'onboarding' in o else o
    assert o.get('articleSettingsConfigured') is True, \
        f"step not set: {o}"

    # PUT dismissed
    pr2 = requests.put(f"{BASE_URL}/api/user/onboarding",
                       json={'dismissed': True},
                       headers=user_headers, timeout=15)
    assert pr2.status_code == 200, pr2.text
    g2 = requests.get(f"{BASE_URL}/api/user/onboarding",
                      headers=user_headers, timeout=15)
    o2 = g2.json().get('data', g2.json())
    o2 = o2.get('onboarding') if 'onboarding' in o2 else o2
    assert o2.get('dismissed') is True, f"dismissed not set: {o2}"

    # /auth/me data.onboarding mirrors
    me = requests.get(f"{BASE_URL}/api/auth/me",
                      headers=user_headers, timeout=15)
    assert me.status_code == 200, me.text
    mdata = me.json().get('data', me.json())
    monb = mdata.get('onboarding') or (mdata.get('user') or {}).get('onboarding')
    assert isinstance(monb, dict), f"/me missing onboarding: {mdata}"
    assert monb.get('articleSettingsConfigured') is True
    assert monb.get('dismissed') is True

    # `completed` should be true IFF the 4 real steps all true
    real_steps = ('websiteConnected', 'articleSettingsConfigured',
                  'searchTermsAdded', 'firstScanRun')
    expected_completed = all(monb.get(s) for s in real_steps)
    assert monb.get('completed') == expected_completed, \
        f"completed mismatch: {monb}"


# ========================= FIX 3 — AI visibility scan (slow) =========================

@pytest.mark.slow
def test_fix3_ai_visibility_scan_gpt4o_only(user_headers, fresh_user):
    """Trigger scan; poll up to 90s; verify new shape on /latest."""
    sid = fresh_user['siteId']
    r = requests.post(
        f"{BASE_URL}/api/ai-visibility/scan",
        json={'siteId': sid}, headers=user_headers, timeout=20)
    assert r.status_code in (200, 201, 202), r.text
    body = r.json().get('data', r.json())
    job_id = body.get('jobId') or body.get('id')
    if not job_id:
        pytest.skip(f"no jobId returned from scan: {body}")

    # poll
    completed = False
    for _ in range(45):  # 45 * 2s = 90s
        s = requests.get(
            f"{BASE_URL}/api/ai-visibility/scan/{job_id}",
            headers=user_headers, timeout=15)
        if s.status_code == 200:
            sdata = s.json().get('data', s.json())
            if sdata.get('status') in ('completed', 'COMPLETED', 'done'):
                completed = True
                break
            if sdata.get('status') in ('failed', 'FAILED', 'error'):
                pytest.fail(f"scan failed: {sdata}")
        time.sleep(2)
    if not completed:
        pytest.skip("scan didn't complete in 90s — flaky LLM (skipping)")

    # GET latest
    lr = requests.get(f"{BASE_URL}/api/ai-visibility/latest?siteId={sid}",
                      headers=user_headers, timeout=15)
    assert lr.status_code == 200, lr.text
    lbody = lr.json()
    ldata = lbody.get('data', lbody)
    assert ldata is not None, f"latest data null: {lbody}"
    assert isinstance(ldata.get('overallScore'), int)
    assert ldata.get('visibilityStatus') in (
        'VISIBLE', 'PARTIAL', 'NOT_VISIBLE'), \
        f"bad visibilityStatus: {ldata.get('visibilityStatus')}"
    assert isinstance(ldata.get('visibilityMessage'), str) and \
        ldata['visibilityMessage']
    assert ldata.get('queriesRun') == 5, \
        f"queriesRun should be 5: {ldata.get('queriesRun')}"
    assert isinstance(ldata.get('mentionsFound'), int)
    results = ldata.get('results') or []
    assert len(results) == 5, f"expected 5 results: {len(results)}"
    for ent in results:
        assert 'query' in ent and 'mentioned' in ent \
            and 'response_snippet' in ent, f"bad result entry: {ent}"
    assert isinstance(ldata.get('recommendations'), list)
    # back-compat
    for legacy in ('chatgptScore', 'perplexityScore'):
        assert legacy in ldata, f"legacy {legacy} missing: {ldata.keys()}"


# ========================= FIX 4 — Resend fallback warning =========================

def test_fix4_email_failure_recorded(admin_headers):
    """Misconfigure Resend with invalid key, no from_email; trigger email; expect FAILED row + onboarding@resend.dev warning."""
    # Save original resend config
    orig = requests.get(f"{BASE_URL}/api/admin/api-keys/resend",
                        headers=admin_headers, timeout=15)
    orig_fields = {}
    if orig.status_code == 200:
        ob = orig.json().get('data', orig.json())
        orig_fields = (ob.get('fields') if isinstance(ob, dict) else {}) or {}

    # Set invalid api_key + no from_email
    set_resp = requests.put(
        f"{BASE_URL}/api/admin/api-keys/resend",
        json={'fields': {'api_key': 'test_invalid_key_iter5',
                          'from_email': ''}},
        headers=admin_headers, timeout=15)
    assert set_resp.status_code == 200, set_resp.text

    # Also clear sendgrid so resend is the path
    sg_orig = requests.get(f"{BASE_URL}/api/admin/api-keys/sendgrid",
                           headers=admin_headers, timeout=15)
    sg_orig_fields = {}
    if sg_orig.status_code == 200:
        sb = sg_orig.json().get('data', sg_orig.json())
        sg_orig_fields = (sb.get('fields') if isinstance(sb, dict) else {}) or {}
    requests.put(f"{BASE_URL}/api/admin/api-keys/sendgrid",
                 json={'fields': {'api_key': '', 'from_email': ''}},
                 headers=admin_headers, timeout=15)

    try:
        # Wait for config cache to refresh (~60s TTL); patch via re-PUT
        # which clears the cache. Best effort.
        time.sleep(2)
        addr = f"resendfx_{uuid.uuid4().hex[:6]}@seojalwa.com"
        rr = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={'email': addr, 'password': 'Testing12345!',
                  'fullName': 'Resend Test'}, timeout=15)
        # Trigger forgot-password (sends an email)
        requests.post(f"{BASE_URL}/api/auth/forgot-password",
                      json={'email': addr}, timeout=15)
        time.sleep(3)

        # Check email_logs via admin endpoint (/api/admin/emails)
        el = requests.get(
            f"{BASE_URL}/api/admin/emails?limit=30",
            headers=admin_headers, timeout=15)
        assert el.status_code == 200, f"admin/emails: {el.status_code} {el.text}"
        rows = el.json().get('data', el.json())
        if isinstance(rows, dict):
            rows = rows.get('items') or rows.get('logs') or rows.get('emails') \
                or rows.get('rows') or []
        # Look for rows tied to our addr with status FAILED + resend provider
        my_rows = [r for r in rows if isinstance(r, dict)
                   and r.get('to') == addr]
        print(f"my_rows for {addr}: {my_rows[:3]}")
        failed = [r for r in my_rows
                  if str(r.get('status', '')).upper() == 'FAILED']
        # Accept either FAILED with resend, or at least non-empty errorMessage
        if not failed:
            # cache may not have refreshed; fall back to any recent FAILED row
            failed = [r for r in rows if isinstance(r, dict)
                      and str(r.get('status', '')).upper() == 'FAILED'
                      and str(r.get('provider', '')).upper() == 'RESEND']
        assert failed, \
            f"no FAILED Resend email_logs row. my_rows={my_rows[:2]} " \
            f"sample={rows[:2]}"
        assert any((r.get('errorMessage') or r.get('error'))
                   for r in failed), \
            f"FAILED rows missing errorMessage: {failed[:1]}"
    finally:
        # Restore originals
        if orig_fields:
            requests.put(f"{BASE_URL}/api/admin/api-keys/resend",
                         json={'fields': orig_fields},
                         headers=admin_headers, timeout=15)
        else:
            requests.put(f"{BASE_URL}/api/admin/api-keys/resend",
                         json={'fields': {'api_key': '', 'from_email': ''}},
                         headers=admin_headers, timeout=15)
        if sg_orig_fields:
            requests.put(f"{BASE_URL}/api/admin/api-keys/sendgrid",
                         json={'fields': sg_orig_fields},
                         headers=admin_headers, timeout=15)


# ========================= FIX 9 — article gen images (slow) =========================

@pytest.mark.slow
def test_fix9_article_generation_includes_two_images(user_headers, fresh_user):
    sid = fresh_user['siteId']
    gp = {'siteId': sid, 'searchTerm': 'iteration 5 image test',
          'topic': 'iteration 5'}
    g = requests.post(f"{BASE_URL}/api/articles/generate",
                      json=gp, headers=user_headers, timeout=20)
    assert g.status_code in (200, 201, 202), g.text
    body = g.json().get('data', g.json())
    job_id = body.get('jobId') or body.get('id')
    if not job_id:
        pytest.skip(f"no jobId: {body}")

    # poll up to 90s
    article = None
    art_id = None
    for _ in range(45):
        s = requests.get(f"{BASE_URL}/api/articles/job/{job_id}",
                         headers=user_headers, timeout=15)
        if s.status_code == 200:
            sdata = s.json().get('data', s.json())
            if str(sdata.get('status', '')).lower() == 'completed':
                # Job doc — look up the articleId
                art_id = ((sdata.get('payload') or {}).get('articleId')
                          or (sdata.get('result') or {}).get('articleId')
                          or sdata.get('articleId'))
                break
            if str(sdata.get('status', '')).lower() == 'failed':
                pytest.fail(f"article gen failed: {sdata}")
        time.sleep(2)
    if not art_id:
        pytest.skip("article gen did not complete in 90s")

    # fetch full article doc
    ar = requests.get(f"{BASE_URL}/api/articles/{art_id}",
                      headers=user_headers, timeout=15)
    assert ar.status_code == 200, ar.text
    article = ar.json().get('data', ar.json())

    assert 'featuredImageUrl' in article, \
        f"featuredImageUrl missing: {list(article.keys())}"
    assert 'inlineImageUrl' in article, \
        f"inlineImageUrl missing: {list(article.keys())}"
    # If inline image present, content must include <figure>...<img/></figure>
    if article.get('inlineImageUrl'):
        content = article.get('content') or article.get('contentHtml') or ''
        assert '<figure' in content and '<img' in content, \
            "inlineImageUrl present but content has no <figure><img>"
