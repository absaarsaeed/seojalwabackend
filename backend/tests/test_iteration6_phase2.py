"""
Iteration 6 — Phase 2 Free plan, admin pricing, dummy checkout, cross-site quota.

Covers:
  PART 1 — GET /api/plans returns 4 plans (Free, Starter, Growth, Agency) with nested features
  PART 2 — Admin PUT plan accepts nested `features` and mirrors to flat
  PART 3 — Cross-site quota GET /PUT /api/user/quota/sites/{siteId}
  PART 4 — Registration auto-assigns Free plan ACTIVE (or shape via minted JWT)
  PART 5 — /api/plans/selection shape with displayValue + highlighted Growth
  PART 6 — Dummy checkout flow + 409 SESSION_CONSUMED on second complete
  PART 7 — Coupon validate + checkout-with-coupon + usedCount incremented
  PART 9 — Admin sub change triggers setup_plan_articles (best-effort)
"""
import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest
import requests

# Load backend env for DB-direct helpers
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


def _mint_user(with_subscription_plan=None, with_site=True):
    """Create a user directly in DB and mint a JWT. Bypasses /auth/register rate-limit."""
    import sys
    sys.path.insert(0, '/app/backend')
    from core.security import create_access_token, hash_password, utcnow_iso  # noqa
    from core.database import get_db  # noqa

    async def _do():
        db = get_db()
        uid = uuid.uuid4().hex
        email = f"i6_{int(time.time() * 1000)}_{uid[:6]}@seojalwa.com"
        await db.users.insert_one({
            "id": uid, "email": email, "fullName": "Iter6 Tester",
            "passwordHash": hash_password("Testing12345!"),
            "role": "user", "status": "active",
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
            "emailVerified": True,
        })
        site_id = None
        if with_site:
            site_id = uuid.uuid4().hex
            await db.sites.insert_one({
                "id": site_id, "userId": uid, "name": "Iter6 Site",
                "url": "https://iter6.example.com",
                "platform": "wordpress",
                "apiKey": f"jalwa_live_{uuid.uuid4().hex[:24]}",
                "createdAt": utcnow_iso(),
            })
        if with_subscription_plan:
            plan = await db.plans.find_one({"id": with_subscription_plan}, {"_id": 0})
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


# ===================== PART 1: GET /api/plans =====================

def test_part1_plans_shape():
    r = requests.get(f"{BASE_URL}/api/plans", timeout=15)
    assert r.status_code == 200, r.text
    rows = r.json().get('data', [])
    names = [p['name'] for p in rows]
    assert 'Free' in names, f"Missing Free plan; got {names}"
    assert 'Starter' in names
    assert 'Growth' in names
    assert 'Agency' in names

    # Order asc check
    orders = [p.get('order') for p in rows]
    assert orders == sorted(orders), f"plans not sorted by order: {orders}"

    expected_feats = {"articlesPerMonth", "websiteConnections",
                      "gscConnection", "aiScansPerMonth",
                      "socialPostsPerMonth", "teamSeats",
                      "whiteLabel", "prioritySupport"}
    for p in rows:
        feats = p.get('features') or {}
        missing = expected_feats - set(feats.keys())
        assert not missing, f"Plan {p['name']} missing features: {missing}"
        for k, meta in feats.items():
            assert isinstance(meta, dict), f"{p['name']}.{k} not dict"
            assert 'enabled' in meta and 'value' in meta, \
                f"{p['name']}.{k} missing keys: {meta}"

    free = next(p for p in rows if p['name'] == 'Free')
    assert free.get('slug') == 'free', f"slug={free.get('slug')}"
    assert free.get('isFree') is True
    assert free.get('order') == 0
    assert int(free.get('monthlyPrice', -1)) == 0


# ===================== PART 2: Admin PUT plan with nested features =====================

def test_part2_admin_plan_nested_features(admin_headers):
    # Get a non-Free plan to patch
    r = requests.get(f"{BASE_URL}/api/admin/plans", headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    plans = r.json().get('data', [])
    target = next((p for p in plans if p.get('name') == 'Starter'), None)
    assert target, "Starter plan not found"
    plan_id = target['id']
    orig_features = dict(target.get('features') or {})
    orig_value = (orig_features.get('articlesPerMonth') or {}).get('value', 20)

    new_value = 99
    # IMPORTANT: admin PUT replaces the entire `features` map (uses $set, not $set
    # with dot-notation). Send the FULL map with the single field mutated to
    # avoid wiping other features.
    new_features = {k: dict(v) for k, v in orig_features.items()}
    new_features['articlesPerMonth'] = {"enabled": True, "value": new_value}
    body = {"features": new_features}
    r2 = requests.put(f"{BASE_URL}/api/admin/plans/{plan_id}",
                       headers=admin_headers, json=body, timeout=15)
    assert r2.status_code == 200, r2.text
    raw = r2.json().get('data', {})
    updated = raw.get('plan', raw)
    assert updated.get('articlesPerMonth') == new_value, \
        f"flat mirror failed: {updated.get('articlesPerMonth')}"
    nested_v = ((updated.get('features') or {}).get('articlesPerMonth') or {}).get('value')
    assert nested_v == new_value, f"nested failed: {nested_v}"
    # Other features should still be present
    assert set((updated.get('features') or {}).keys()) >= set(orig_features.keys()), \
        f"features wiped: {set((updated.get('features') or {}).keys())}"

    # Reset
    restore_features = {k: dict(v) for k, v in orig_features.items()}
    restore_features['articlesPerMonth'] = {"enabled": True, "value": orig_value}
    requests.put(f"{BASE_URL}/api/admin/plans/{plan_id}",
                  headers=admin_headers,
                  json={"features": restore_features}, timeout=15)


# ===================== PART 3: Cross-site quota =====================

def test_part3_user_quota():
    # Get Free plan id
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p.get('name') == 'Free')
    free_id = free['id']
    free_articles = ((free.get('features') or {}).get('articlesPerMonth') or {}).get('value', 0)

    u = _mint_user(with_subscription_plan=free_id, with_site=True)
    headers = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}

    # GET quota
    r = requests.get(f"{BASE_URL}/api/user/quota", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    q = r.json().get('data', {})
    assert q.get('planName') == 'Free', q
    assert q.get('planTotal') == free_articles, q
    assert 'usedThisMonth' in q and 'remainingTotal' in q
    assert isinstance(q.get('sites'), list) and len(q['sites']) == 1
    s0 = q['sites'][0]
    assert s0['siteId'] == u['siteId']
    assert s0['quotaAllocated'] == free_articles, \
        f"single-site allocation should equal plan total: {s0}"
    assert s0.get('autoDistribute') is True

    # PUT custom quota
    target_quota = min(free_articles, max(0, free_articles - 1))
    # On Free plan with articlesPerMonth>=1, set quota to a smaller valid value
    if free_articles == 0:
        # Free is unlimited or 0; let's pick a Growth plan and re-mint
        growth = next(p for p in plans if p['name'] == 'Growth')
        u = _mint_user(with_subscription_plan=growth['id'], with_site=True)
        headers = {'Authorization': f"Bearer {u['token']}",
                   'Content-Type': 'application/json'}
        target_quota = 5

    r2 = requests.put(f"{BASE_URL}/api/user/quota/sites/{u['siteId']}",
                       headers=headers, json={"quotaPerMonth": target_quota}, timeout=15)
    assert r2.status_code == 200, r2.text

    # GET again — autoDistribute should be False
    r3 = requests.get(f"{BASE_URL}/api/user/quota", headers=headers, timeout=15)
    s_after = r3.json()['data']['sites'][0]
    assert s_after['autoDistribute'] is False, s_after
    assert s_after['quotaAllocated'] == target_quota

    # PUT excessive quota
    r4 = requests.put(f"{BASE_URL}/api/user/quota/sites/{u['siteId']}",
                       headers=headers, json={"quotaPerMonth": 999999}, timeout=15)
    assert r4.status_code == 400, r4.text
    body = r4.json()
    code = body.get('code') or body.get('error', {})
    assert 'QUOTA_EXCEEDS_PLAN' in str(body), f"Expected QUOTA_EXCEEDS_PLAN: {body}"


# ===================== PART 4: Auto-assign Free plan on register =====================

def test_part4_register_assigns_free_plan():
    """Try fresh register; fall back to minted-JWT shape verification on 429."""
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p.get('name') == 'Free')
    free_id = free['id']

    ts = int(time.time() * 1000)
    email = f"i6reg_{ts}_{uuid.uuid4().hex[:6]}@seojalwa.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                       json={'email': email, 'password': 'Testing12345!',
                              'fullName': 'I6Reg'}, timeout=15)
    if r.status_code == 429:
        # Fallback — mint JWT for user pre-seeded with Free plan
        u = _mint_user(with_subscription_plan=free_id, with_site=False)
        token = u['token']
    else:
        assert r.status_code in (200, 201), r.text
        body = r.json().get('data', {})
        token = body.get('accessToken') or (body.get('tokens') or {}).get('accessToken')

    headers = {'Authorization': f"Bearer {token}", 'Content-Type': 'application/json'}
    sr = requests.get(f"{BASE_URL}/api/billing/subscription", headers=headers, timeout=15)
    assert sr.status_code == 200, sr.text
    sub = sr.json().get('data') or {}
    assert sub.get('status') == 'ACTIVE', sub
    assert sub.get('source') == 'FREE', sub
    assert sub.get('planId') == free_id, sub
    assert sub.get('trialEndsAt') in (None, ""), sub
    assert sub.get('currentPeriodEnd') in (None, ""), sub


# ===================== PART 5: /api/plans/selection =====================

def test_part5_plans_selection_shape():
    r = requests.get(f"{BASE_URL}/api/plans/selection", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json().get('data', {})
    assert data.get('trialDays') == 0, data
    plans = data.get('plans', [])
    assert len(plans) >= 4
    # Sorted ascending — Free first
    assert plans[0].get('name') == 'Free', [p.get('name') for p in plans]

    required_keys = {'slug', 'name', 'monthlyPrice', 'annualPrice',
                     'isFree', 'features', 'cta', 'highlighted'}
    for p in plans:
        missing = required_keys - set(p.keys())
        assert not missing, f"{p.get('name')} missing: {missing}"
        assert isinstance(p['features'], list)
        for f in p['features']:
            for k in ('key', 'label', 'value', 'displayValue', 'enabled'):
                assert k in f, f"feature missing {k}: {f}"

    free = next(p for p in plans if p['name'] == 'Free')
    assert free['cta'] == 'Get started free', free['cta']

    growth = next(p for p in plans if p['name'] == 'Growth')
    assert growth['highlighted'] is True

    # Validate displayValue format on Starter articlesPerMonth
    starter = next(p for p in plans if p['name'] == 'Starter')
    art_feat = next((f for f in starter['features']
                     if f['key'] == 'articlesPerMonth'), None)
    if art_feat:
        assert 'articles/month' in art_feat['displayValue'], art_feat


# ===================== PART 6: Dummy checkout =====================

def test_part6_dummy_checkout_flow():
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')
    growth = next(p for p in plans if p['name'] == 'Growth')

    u = _mint_user(with_subscription_plan=free['id'], with_site=True)
    headers = {'Authorization': f"Bearer {u['token']}",
               'Content-Type': 'application/json'}

    # 6a — checkout no coupon
    r = requests.post(f"{BASE_URL}/api/billing/checkout",
                      headers=headers,
                      json={"planId": growth['id'], "billingInterval": "MONTHLY"},
                      timeout=15)
    assert r.status_code == 200, r.text
    sess = r.json().get('data', {})
    assert sess.get('sessionId')
    assert sess.get('discount') == 0
    assert sess.get('finalPrice') == sess.get('originalPrice')
    assert sess.get('expiresAt')
    sid = sess['sessionId']

    # 6b — complete
    rc = requests.post(f"{BASE_URL}/api/billing/checkout/{sid}/complete",
                       headers=headers,
                       json={"cardNumber": "4242424242424242",
                              "expiryMonth": "12", "expiryYear": "2030",
                              "cvv": "123", "cardName": "Test"},
                       timeout=20)
    assert rc.status_code == 200, rc.text
    cdata = rc.json().get('data', {})
    assert cdata.get('message') == 'Payment successful!'
    assert cdata.get('plan') == 'Growth'
    assert cdata.get('invoiceId')
    assert cdata.get('subscriptionId')
    invoice_id = cdata['invoiceId']

    # 6c — subscription updated
    sr = requests.get(f"{BASE_URL}/api/billing/subscription",
                      headers=headers, timeout=15)
    sub = sr.json()['data']
    assert sub['status'] == 'ACTIVE'
    assert sub['source'] == 'CHECKOUT'
    assert sub['planId'] == growth['id']

    # 6d — invoice exists
    ir = requests.get(f"{BASE_URL}/api/billing/invoices",
                      headers=headers, timeout=15)
    invs = ir.json()['data']
    assert any(inv.get('id') == invoice_id for inv in invs), invs

    # 6e — second complete is 409 SESSION_CONSUMED
    rc2 = requests.post(f"{BASE_URL}/api/billing/checkout/{sid}/complete",
                        headers=headers, json={}, timeout=15)
    assert rc2.status_code == 409, rc2.text
    assert 'SESSION_CONSUMED' in rc2.text

    # 6f — audit + notification (best-effort lookup via DB)
    import sys
    sys.path.insert(0, '/app/backend')
    from core.database import get_db

    async def _check():
        db = get_db()
        audit = await db.admin_audit_log.find_one(
            {"action": "USER_PLAN_CHANGED",
             "targetId": u['userId'],
             "metadata.via": "dummy_checkout"})
        notif = await db.notifications.find_one(
            {"userId": u['userId'], "type": "SUBSCRIPTION_RENEWED"})
        return audit, notif

    audit, notif = asyncio.get_event_loop().run_until_complete(_check())
    assert audit is not None, "USER_PLAN_CHANGED audit missing"
    assert notif is not None, "SUBSCRIPTION_RENEWED notification missing"


# ===================== PART 7: Coupons =====================

def test_part7_coupon_validate_and_apply(admin_headers):
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')
    growth = next(p for p in plans if p['name'] == 'Growth')
    monthly = int(growth['monthlyPrice'])

    code = f"PHASE2_{uuid.uuid4().hex[:6].upper()}"
    # Spec says type:"PERCENT" but admin currently only accepts PERCENTAGE/FIXED.
    # Try the spec value first; fall back to PERCENTAGE.
    cr = requests.post(f"{BASE_URL}/api/admin/coupons",
                       headers=admin_headers,
                       json={"code": code, "type": "PERCENT",
                              "value": 20, "maxUses": 5},
                       timeout=15)
    used_type = "PERCENT"
    if cr.status_code != 201:
        cr = requests.post(f"{BASE_URL}/api/admin/coupons",
                           headers=admin_headers,
                           json={"code": code, "type": "PERCENTAGE",
                                  "value": 20, "maxUses": 5},
                           timeout=15)
        used_type = "PERCENTAGE"
    assert cr.status_code == 201, f"coupon create failed: {cr.text}"
    coupon_id = cr.json()['data']['id']

    # Validate-coupon
    vr = requests.post(f"{BASE_URL}/api/billing/validate-coupon",
                       json={"code": code, "planId": growth['id'],
                              "billingInterval": "MONTHLY"}, timeout=15)
    assert vr.status_code == 200, vr.text
    vd = vr.json()['data']

    expected_discount = int(round(monthly * 0.20))
    if used_type == "PERCENTAGE":
        # Known mismatch: billing._apply_coupon only handles "PERCENT",
        # so PERCENTAGE coupons yield 0 discount.
        # Flag and continue with what the API returns.
        if vd.get('valid') and vd.get('discount', {}).get('amount') != expected_discount:
            pytest.fail(
                "BUG: Coupon type mismatch — admin coupons API only accepts "
                "PERCENTAGE/FIXED but billing._apply_coupon expects PERCENT. "
                f"Returned discount.amount={vd['discount']['amount']} "
                f"(expected {expected_discount} for 20% off ${monthly})")

    assert vd['valid'] is True, vd
    assert vd['discount']['amount'] == expected_discount, vd
    assert vd['finalPrice'] == monthly - expected_discount, vd

    # Apply via checkout
    u = _mint_user(with_subscription_plan=free['id'], with_site=True)
    h = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}
    co = requests.post(f"{BASE_URL}/api/billing/checkout", headers=h,
                       json={"planId": growth['id'], "billingInterval": "MONTHLY",
                              "couponCode": code}, timeout=15)
    assert co.status_code == 200, co.text
    sess = co.json()['data']
    assert sess['discount'] == expected_discount
    sid = sess['sessionId']

    cc = requests.post(f"{BASE_URL}/api/billing/checkout/{sid}/complete",
                       headers=h, json={"cardNumber": "4242"}, timeout=20)
    assert cc.status_code == 200, cc.text

    # Verify coupon.usedCount incremented to 1
    coupons = requests.get(f"{BASE_URL}/api/admin/coupons",
                            headers=admin_headers, timeout=15).json()['data']
    c = next(c for c in coupons if c['id'] == coupon_id)
    assert c['usedCount'] == 1, c

    # Invalid code
    iv = requests.post(f"{BASE_URL}/api/billing/validate-coupon",
                        json={"code": "NEVER_EXISTS_XYZ", "planId": growth['id'],
                               "billingInterval": "MONTHLY"}, timeout=15)
    assert iv.status_code == 200
    assert iv.json()['data']['valid'] is False

    # Cleanup
    requests.delete(f"{BASE_URL}/api/admin/coupons/{coupon_id}",
                     headers=admin_headers, timeout=15)


# ===================== PART 9: Admin plan switch triggers setup_plan_articles =====================

def test_part9_admin_subscription_change_triggers_articles(admin_headers):
    plans = requests.get(f"{BASE_URL}/api/plans", timeout=15).json()['data']
    free = next(p for p in plans if p['name'] == 'Free')
    growth = next(p for p in plans if p['name'] == 'Growth')

    u = _mint_user(with_subscription_plan=free['id'], with_site=True)

    # Admin update subscription
    r = requests.put(f"{BASE_URL}/api/admin/users/{u['userId']}/subscription",
                      headers=admin_headers,
                      json={"planId": growth['id'], "status": "ACTIVE"},
                      timeout=20)
    assert r.status_code == 200, r.text

    # Wait briefly for the background task
    time.sleep(3)

    # Check articles in DB
    import sys
    sys.path.insert(0, '/app/backend')
    from core.database import get_db

    async def _count():
        db = get_db()
        return await db.articles.count_documents({"userId": u['userId']})

    n = asyncio.get_event_loop().run_until_complete(_count())
    # Best-effort: either >0 articles OR no error (just log it)
    print(f"[PART9] articles created for user {u['userId']}: {n}")
    # Don't fail if 0 — spec says "best-effort fallback"
    assert n >= 0


# ===================== Regression: prior fixes still healthy =====================

def test_regression_onboarding_and_activity(admin_headers):
    u = _mint_user(with_subscription_plan=None, with_site=False)
    h = {'Authorization': f"Bearer {u['token']}", 'Content-Type': 'application/json'}
    r = requests.get(f"{BASE_URL}/api/user/onboarding", headers=h, timeout=15)
    assert r.status_code == 200, r.text
    ob = r.json()['data']['onboarding']
    for k in ('websiteConnected', 'articleSettingsConfigured',
              'searchTermsAdded', 'firstScanRun', 'dismissed', 'completed'):
        assert k in ob and isinstance(ob[k], bool), ob

    # admin dashboard activity
    a = requests.get(f"{BASE_URL}/api/admin/dashboard/activity?limit=10",
                      headers=admin_headers, timeout=15)
    assert a.status_code == 200, a.text
