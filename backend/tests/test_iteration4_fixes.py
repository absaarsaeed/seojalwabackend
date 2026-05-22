"""
Iteration 4 — validates 11 specific fixes/flows requested by main agent.
Covers: admin user detail enrichment, dashboard reshape (incl. empty state),
growth-score empty-state, ai-visibility/latest, analytics/overview reshape,
GSC connect (await-on-None bug), articles/calendar shape, activity logging,
admin subscription update notification+log, cascade delete.
"""
import os, time, uuid, requests, pytest
from pathlib import Path

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


# ---------- session-scoped fixtures ----------

@pytest.fixture(scope='session')
def admin_token():
    r = requests.post(f"{BASE_URL}/api/admin/auth/login",
                      json={'username': ADMIN_USER, 'password': ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.json().get('data', {}).get('token') or r.json().get('token')
    assert tok
    return tok


@pytest.fixture(scope='session')
def admin_headers(admin_token):
    return {'X-Admin-Token': admin_token, 'Content-Type': 'application/json'}


def _register(create_site=True):
    """Create a fresh user; returns dict(token, userId, siteId, email)."""
    ts = int(time.time() * 1000)
    email = f"fix_{ts}_{uuid.uuid4().hex[:6]}@seojalwa.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={'email': email, 'password': 'Testing12345!', 'fullName': 'Fix Tester'},
                      timeout=15)
    assert r.status_code in (200, 201), r.text
    body = r.json()
    data = body.get('data', body)
    token = data.get('accessToken') or data.get('token') or data['tokens']['accessToken']
    user = data.get('user') or {}
    user_id = user.get('id') or user.get('_id')
    sites = data.get('sites') or []
    site_id = sites[0]['id'] if sites else None
    if not site_id and create_site:
        # Create a site explicitly since auto-site is gone
        sp = {'name': 'Auto Test', 'url': f"https://auto-{uuid.uuid4().hex[:6]}.com",
              'platform': 'WORDPRESS'}
        sr = requests.post(f"{BASE_URL}/api/sites", json=sp,
                           headers={'Authorization': f'Bearer {token}',
                                    'Content-Type': 'application/json'}, timeout=15)
        if sr.status_code in (200, 201):
            sd = sr.json().get('data', sr.json())
            site_id = sd.get('id') or sd.get('_id')
    return {'token': token, 'userId': user_id, 'siteId': site_id, 'email': email}


@pytest.fixture(scope='session')
def fresh_user():
    return _register()


@pytest.fixture(scope='session')
def user_headers(fresh_user):
    return {'Authorization': f"Bearer {fresh_user['token']}", 'Content-Type': 'application/json'}


# ---------- FIX 1: admin user detail enrichment ----------

def test_fix1_admin_user_detail_enriched(admin_headers, fresh_user):
    uid = fresh_user['userId']
    assert uid, 'userId missing from registration'
    r = requests.get(f"{BASE_URL}/api/admin/users/{uid}", headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json().get('data', r.json())
    # subscription.plan populated
    sub = data.get('subscription') or {}
    plan = sub.get('plan') or {}
    assert isinstance(plan, dict) and plan.get('name'), f"subscription.plan not populated: {sub}"
    # usage block
    usage = data.get('usage') or {}
    for k in ('articlesThisMonth', 'socialPostsThisMonth', 'aiScansThisMonth', 'teamSeatsUsed'):
        assert k in usage, f"usage missing key {k}: {usage}"
    # stats block
    stats = data.get('stats') or {}
    for k in ('totalArticles', 'totalClicks', 'totalScans', 'growthScore'):
        assert k in stats, f"stats missing key {k}: {stats}"


# ---------- FIX 2: dashboard overview reshape ----------

def test_fix2_dashboard_overview_shape(user_headers, fresh_user):
    sid = fresh_user['siteId']
    assert sid
    r = requests.get(f"{BASE_URL}/api/dashboard/overview?siteId={sid}",
                     headers=user_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json().get('data', r.json())

    gs = data.get('growthScore') or {}
    assert 'score' in gs and 'change' in gs and 'breakdown' in gs, f"growthScore shape: {gs}"
    bd = gs['breakdown']
    for k in ('aiVisibility', 'seoContent', 'socialConsistency', 'trafficTrend'):
        assert k in bd, f"breakdown missing {k}: {bd}"

    assert 'nextScheduledArticle' in data
    assert 'topPerformingArticle' in data
    for k in ('hasConnectedSite', 'hasGeneratedArticle', 'hasRunScan'):
        assert k in data and isinstance(data[k], bool), f"{k} missing or not bool"

    trial = data.get('trial') or {}
    assert 'isTrialing' in trial
    if trial.get('isTrialing'):
        assert 'daysRemaining' in trial and 'trialEndsAt' in trial

    metrics = data.get('metrics') or {}
    for k in ('articlesThisMonth', 'articlesPublished', 'totalClicks',
              'avgPosition', 'aiVisibilityScore'):
        assert k in metrics, f"metrics missing {k}: {metrics}"


# ---------- FIX 2b: dashboard overview empty state ----------

def test_fix2b_dashboard_empty_state_no_404():
    # Register a brand new user without a site
    u = _register(create_site=False)
    h = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}
    if u['siteId']:
        d = requests.delete(f"{BASE_URL}/api/sites/{u['siteId']}", headers=h, timeout=15)
        assert d.status_code in (200, 204), d.text
    r = requests.get(f"{BASE_URL}/api/dashboard/overview", headers=h, timeout=15)
    assert r.status_code == 200, f"expected 200 not 404: {r.status_code} {r.text}"
    data = r.json().get('data', r.json())
    assert data.get('hasConnectedSite') is False
    assert data.get('site') in (None, {}, ), f"site should be null when no site: {data.get('site')}"
    metrics = data.get('metrics') or {}
    for k, v in metrics.items():
        if isinstance(v, (int, float)):
            assert v == 0, f"metric {k} should be 0 in empty state, got {v}"
    recs = data.get('recommendations') or []
    joined = ' '.join(
        (r.get('title', '') + ' ' + r.get('message', '')) if isinstance(r, dict) else str(r)
        for r in recs
    ).lower()
    assert 'connect' in joined and 'site' in joined, f"missing onboarding rec: {recs}"


# ---------- FIX 3: growth-score empty state ----------

def test_fix3_growth_score_empty_state(user_headers, fresh_user):
    sid = fresh_user['siteId']
    r = requests.get(f"{BASE_URL}/api/growth-score?siteId={sid}",
                     headers=user_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    data = body.get('data', body)
    assert data is not None, "data must not be null"
    assert data.get('score', None) == 0, f"score should be 0 first call: {data.get('score')}"
    bd = data.get('breakdown') or {}
    assert all(bd.get(k) == 0 for k in
               ('aiVisibility', 'seoContent', 'socialConsistency', 'trafficTrend')), bd
    assert data.get('history') == [], f"history must be []: {data.get('history')}"
    msg = (body.get('message') or data.get('message') or '').lower()
    assert 'first ai scan' in msg or 'run your first' in msg, f"missing onboarding message: {msg}"


# ---------- FIX 4: ai-visibility latest ----------

def test_fix4_ai_visibility_latest_no_scan(user_headers, fresh_user):
    sid = fresh_user['siteId']
    r = requests.get(f"{BASE_URL}/api/ai-visibility/latest?siteId={sid}",
                     headers=user_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    # data null is required by spec
    assert body.get('data') is None, f"data should be null: {body}"
    msg = (body.get('message') or '').lower()
    assert 'no scans' in msg, f"expected 'No scans yet': {msg}"


# ---------- FIX 5: analytics overview reshape ----------

def test_fix5_analytics_overview_no_gsc(user_headers, fresh_user):
    sid = fresh_user['siteId']
    r = requests.get(f"{BASE_URL}/api/analytics/overview?siteId={sid}",
                     headers=user_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    data = body.get('data', body)
    assert data.get('gscConnected') is False
    metrics = data.get('metrics') or {}
    for k, v in metrics.items():
        if isinstance(v, (int, float)):
            assert v == 0, f"metric {k} should be 0: {v}"
    assert 'trend' in data
    assert data.get('topArticles') == []
    assert data.get('topQueries') == []
    msg = (body.get('message') or data.get('message') or '').lower()
    assert 'google search console' in msg or 'connect' in msg, f"missing GSC message: {msg}"


# ---------- FIX 6: GSC connect await-on-None ----------

def test_fix6_gsc_connect_returns_400_not_500(user_headers):
    r = requests.get(f"{BASE_URL}/api/analytics/gsc/connect",
                     headers=user_headers, timeout=15)
    assert r.status_code == 400, f"Expected 400 GSC_NOT_CONFIGURED, got {r.status_code} {r.text}"
    body = r.json()
    code = body.get('code') or body.get('error', {}).get('code') or ''
    assert 'GSC_NOT_CONFIGURED' in str(body), f"Expected GSC_NOT_CONFIGURED code: {body}"


# ---------- FIX 7: articles calendar shape ----------

def test_fix7_articles_calendar_shape(user_headers, fresh_user):
    sid = fresh_user['siteId']
    r = requests.get(f"{BASE_URL}/api/articles/calendar?siteId={sid}&year=2026&month=5",
                     headers=user_headers, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    data = body.get('data', body)
    assert isinstance(data, dict), f"calendar data should be dict: {type(data)}"
    # values must be lists
    for k, v in data.items():
        assert isinstance(v, list), f"calendar[{k}] should be list, got {type(v)}"


# ---------- FIX 8: activity logging for SITE_ADDED, ARTICLE_GENERATED ----------

def test_fix8_activity_logging_site_added_and_article_generated():
    """Fresh user -> create site -> generate article -> wait -> dashboard recentActivity."""
    u = _register()
    h = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}
    # POST a new site (in addition to the auto-created one) -> SITE_ADDED
    site_payload = {'name': 'Iter4 Test', 'url': f"https://iter4-{uuid.uuid4().hex[:6]}.com",
                    'platform': 'WORDPRESS'}
    sr = requests.post(f"{BASE_URL}/api/sites", json=site_payload, headers=h, timeout=15)
    assert sr.status_code in (200, 201), sr.text
    new_site_id = (sr.json().get('data') or sr.json()).get('id')

    # POST article generation -> ARTICLE_GENERATED (background)
    gp = {'siteId': new_site_id, 'topic': 'iteration 4 activity log test',
          'searchTerm': 'iteration 4'}
    gr = requests.post(f"{BASE_URL}/api/articles/generate", json=gp, headers=h, timeout=15)
    assert gr.status_code in (200, 201, 202), gr.text

    # wait for the BackgroundTask
    time.sleep(10)

    # dashboard overview recentActivity
    do = requests.get(f"{BASE_URL}/api/dashboard/overview?siteId={new_site_id}",
                      headers=h, timeout=15)
    assert do.status_code == 200, do.text
    data = do.json().get('data', {})
    activity = data.get('recentActivity') or []
    actions = [a.get('action') for a in activity if isinstance(a, dict)]
    print(f"recentActivity actions: {actions}")
    assert 'SITE_ADDED' in actions, f"SITE_ADDED missing from {actions}"
    assert 'ARTICLE_GENERATED' in actions, f"ARTICLE_GENERATED missing from {actions}"
    # USER_REGISTERED should also be there (fresh user)
    assert 'USER_REGISTERED' in actions, f"USER_REGISTERED missing from {actions}"


# ---------- FIX 10: admin subscription update -> notification + activity log ----------

def test_fix10_admin_subscription_update_creates_notification_and_log(admin_headers):
    u = _register()
    h = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}

    # find Growth plan id
    pl = requests.get(f"{BASE_URL}/api/plans", timeout=15)
    pj = pl.json()
    pdata = pj.get('data', pj)
    if isinstance(pdata, dict):
        plans = pdata.get('plans') or []
    elif isinstance(pdata, list):
        plans = pdata
    else:
        plans = []
    growth = next((p for p in plans if str(p.get('name', '')).lower() == 'growth'), None)
    assert growth, f"Growth plan not found: {plans}"
    growth_id = growth.get('id') or growth.get('_id')

    # admin updates subscription
    payload = {'planId': growth_id, 'status': 'ACTIVE'}
    ur = requests.put(f"{BASE_URL}/api/admin/users/{u['userId']}/subscription",
                      json=payload, headers=admin_headers, timeout=15)
    assert ur.status_code == 200, ur.text
    udata = ur.json().get('data', ur.json())
    sub = udata.get('subscription') or {}
    plan = sub.get('plan') or {}
    assert isinstance(plan, dict) and plan.get('name', '').lower() == 'growth', \
        f"plan not populated as Growth: {plan}"

    # check notifications
    time.sleep(1)
    nr = requests.get(f"{BASE_URL}/api/notifications", headers=h, timeout=15)
    assert nr.status_code == 200, nr.text
    notifs = (nr.json().get('data') or nr.json()).get('notifications') \
        if isinstance(nr.json().get('data'), dict) else nr.json().get('data') or []
    if isinstance(notifs, dict):
        notifs = notifs.get('notifications', [])
    types = [n.get('type') for n in notifs if isinstance(n, dict)]
    print(f"notification types: {types}")
    titles = [n.get('title', '') for n in notifs if isinstance(n, dict)]
    assert any('SUBSCRIPTION' in str(t).upper() for t in types) or \
        any('subscription' in t.lower() for t in titles), \
        f"No subscription notification found. types={types} titles={titles}"

    # dashboard recentActivity should contain SUBSCRIPTION_UPGRADED
    do = requests.get(f"{BASE_URL}/api/dashboard/overview", headers=h, timeout=15)
    assert do.status_code == 200, do.text
    activity = (do.json().get('data') or {}).get('recentActivity') or []
    actions = [a.get('action') for a in activity if isinstance(a, dict)]
    print(f"recentActivity actions after sub update: {actions}")
    assert 'SUBSCRIPTION_UPGRADED' in actions, f"SUBSCRIPTION_UPGRADED missing: {actions}"


# ---------- FIX 11: cascade delete ----------

def test_fix11_admin_user_cascade_delete(admin_headers):
    u = _register()
    h = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}
    # ensure at least one site (auto-created on register)
    dr = requests.delete(f"{BASE_URL}/api/admin/users/{u['userId']}",
                         headers=admin_headers, timeout=15)
    assert dr.status_code == 200, dr.text
    body = dr.json()
    data = body.get('data', body)
    cd = data.get('cascadedDeletes') or data.get('cascaded') or {}
    print(f"cascadedDeletes: {cd}")
    assert cd.get('users') == 1, f"users:1 expected: {cd}"
    for k in ('sites', 'articles', 'subscriptions'):
        assert k in cd, f"{k} key missing from cascadedDeletes: {cd}"

    # GET should return 404
    g = requests.get(f"{BASE_URL}/api/admin/users/{u['userId']}",
                     headers=admin_headers, timeout=15)
    assert g.status_code == 404, f"expected 404 after delete: {g.status_code} {g.text}"
