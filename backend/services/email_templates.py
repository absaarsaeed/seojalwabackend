"""DB-backed email template store with idempotent seed.

Templates live in the `email_templates` collection. Each call to
`get_template(key)` reads from the DB (with a tiny in-process cache so the
hot path doesn't repeatedly hit Mongo).

Variables are interpolated via `{{name}}` syntax.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from core.database import get_db
from core.security import utcnow_iso

logger = logging.getLogger("jalwa.email_templates")


# ─────────────────────────────────────────────────────────────── seed data
# Minimal but production-ready HTML. Admin can edit any of these in-place.
def _wrap(inner: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f7fb;padding:32px"><table style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(15,23,42,.06)"><tr><td style="padding:32px">{inner}<p style="font-size:13px;color:#64748b;margin-top:32px">— The SEO Jalwa team</p></td></tr></table></body></html>"""  # noqa: E501


SEED_TEMPLATES: list[dict] = [
    {"key": "welcome", "name": "Welcome Email",
     "category": "ONBOARDING",
     "subject": "Welcome to SEO Jalwa, {{userName}}!",
     "variables": ["userName", "loginUrl"],
     "description": "Sent right after a user completes registration.",
     "htmlBody": _wrap(
         "<h1 style='margin:0 0 12px'>Welcome, {{userName}} 👋</h1>"
         "<p>Your SEO Jalwa account is ready. Add your site, drop in a "
         "keyword, and let our AI publish your first article in minutes.</p>"
         "<p><a href='{{loginUrl}}' style='background:#0f172a;color:#fff;"
         "padding:12px 20px;border-radius:8px;text-decoration:none;"
         "display:inline-block'>Open my dashboard</a></p>")},
    {"key": "verify_email", "name": "Verify Email",
     "category": "ONBOARDING",
     "subject": "Verify your email",
     "variables": ["userName", "verifyUrl"],
     "description": "Email-verification step (currently disabled).",
     "htmlBody": _wrap(
         "<h1>Verify your email</h1><p>Hi {{userName}}, click below to "
         "verify.</p><p><a href='{{verifyUrl}}'>Verify email →</a></p>")},
    {"key": "password_reset", "name": "Password Reset",
     "category": "ACCOUNT",
     "subject": "Reset your SEO Jalwa password",
     "variables": ["userName", "resetUrl"],
     "description": "Password reset link with 1 h expiry.",
     "htmlBody": _wrap(
         "<h1>Reset password</h1><p>Hi {{userName}}, click below to set a "
         "new password. The link expires in 1 hour.</p>"
         "<p><a href='{{resetUrl}}' style='background:#0f172a;color:#fff;"
         "padding:12px 20px;border-radius:8px;text-decoration:none;"
         "display:inline-block'>Reset password</a></p>")},
    {"key": "article_published", "name": "Article Published",
     "category": "PRODUCT",
     "subject": "🎉 New article live on {{siteUrl}}",
     "variables": ["userName", "articleTitle", "articleUrl", "siteUrl",
                   "seoScore"],
     "description": "Fired when an article auto-publishes successfully.",
     "htmlBody": _wrap(
         "<h1>Your article is live, {{userName}}!</h1>"
         "<p><strong>{{articleTitle}}</strong> — SEO score "
         "{{seoScore}}/100.</p><p><a href='{{articleUrl}}'>View article →</a>"
         "</p>")},
    {"key": "article_failed", "name": "Article Failed",
     "category": "PRODUCT",
     "subject": "Article generation failed",
     "variables": ["userName", "searchTerm", "errorMessage"],
     "description": "Sent when generation errors out.",
     "htmlBody": _wrap(
         "<h1>Article failed</h1><p>Hi {{userName}}, generation for "
         "'<em>{{searchTerm}}</em>' failed: {{errorMessage}}</p>")},
    {"key": "weekly_digest", "name": "Weekly Digest",
     "category": "REPORTS",
     "subject": "Your weekly SEO Jalwa report",
     "variables": ["userName", "articlesPublished", "avgSeoScore",
                   "growthScore", "dashboardUrl"],
     "description": "Monday 8 AM UTC digest.",
     "htmlBody": _wrap(
         "<h1>Hi {{userName}} — your week in review</h1>"
         "<ul><li>Articles published: <strong>{{articlesPublished}}</strong>"
         "</li><li>Average SEO score: <strong>{{avgSeoScore}}</strong></li>"
         "<li>Growth score: <strong>{{growthScore}}</strong></li></ul>"
         "<p><a href='{{dashboardUrl}}'>Open dashboard →</a></p>")},
    {"key": "ai_scan_complete", "name": "AI Visibility Scan Complete",
     "category": "REPORTS",
     "subject": "Your AI visibility scan is ready",
     "variables": ["userName", "overallScore", "dashboardUrl"],
     "description": "Sent when an AI visibility scan finishes.",
     "htmlBody": _wrap(
         "<h1>Scan complete</h1><p>Your overall AI visibility score is "
         "<strong>{{overallScore}}/100</strong>.</p>"
         "<p><a href='{{dashboardUrl}}'>View full report →</a></p>")},
    {"key": "subscription_created", "name": "Subscription Created",
     "category": "BILLING",
     "subject": "Welcome to {{planName}}",
     "variables": ["userName", "planName", "amount", "nextBilling"],
     "description": "First successful payment / plan activation.",
     "htmlBody": _wrap(
         "<h1>You're on {{planName}}!</h1>"
         "<p>{{userName}}, your subscription is active. Next billing: "
         "{{nextBilling}} for {{amount}}.</p>")},
    {"key": "subscription_renewed", "name": "Subscription Renewed",
     "category": "BILLING",
     "subject": "Subscription renewed",
     "variables": ["userName", "planName", "amount", "nextBilling"],
     "description": "Successful recurring charge.",
     "htmlBody": _wrap(
         "<h1>Renewed</h1><p>{{userName}}, your {{planName}} plan has "
         "renewed for {{amount}}. Next billing: {{nextBilling}}.</p>")},
    {"key": "subscription_cancelled", "name": "Subscription Cancelled",
     "category": "BILLING",
     "subject": "We're sorry to see you go",
     "variables": ["userName", "planName", "accessUntil"],
     "description": "User or admin cancelled the subscription.",
     "htmlBody": _wrap(
         "<h1>Cancelled</h1><p>{{userName}}, you'll keep access to "
         "{{planName}} until {{accessUntil}}.</p>")},
    {"key": "subscription_expiring", "name": "Subscription Expiring Soon",
     "category": "BILLING",
     "subject": "Your subscription expires in {{daysLeft}} days",
     "variables": ["userName", "planName", "daysLeft", "renewUrl"],
     "description": "Sent X days before currentPeriodEnd.",
     "htmlBody": _wrap(
         "<h1>Heads up, {{userName}}</h1><p>Your {{planName}} plan expires "
         "in <strong>{{daysLeft}}</strong> days. Renew to keep auto-"
         "publishing.</p><p><a href='{{renewUrl}}'>Renew now →</a></p>")},
    {"key": "payment_failed", "name": "Payment Failed",
     "category": "BILLING",
     "subject": "Action required — payment failed",
     "variables": ["userName", "billingUrl"],
     "description": "Dunning email on first failed charge.",
     "htmlBody": _wrap(
         "<h1>We couldn't charge your card</h1><p>{{userName}}, please "
         "update your payment method to keep your service active.</p>"
         "<p><a href='{{billingUrl}}'>Update payment →</a></p>")},
    {"key": "team_invite", "name": "Team Invitation",
     "category": "TEAM",
     "subject": "{{inviterName}} invited you to SEO Jalwa",
     "variables": ["inviterName", "teamName", "inviteUrl"],
     "description": "New team-member invitation.",
     "htmlBody": _wrap(
         "<h1>You're invited</h1><p>{{inviterName}} added you to "
         "<strong>{{teamName}}</strong> on SEO Jalwa.</p>"
         "<p><a href='{{inviteUrl}}'>Accept invitation →</a></p>")},
    {"key": "trial_ending", "name": "Trial Ending",
     "category": "BILLING",
     "subject": "Your trial ends in {{daysLeft}} days",
     "variables": ["userName", "daysLeft", "upgradeUrl"],
     "description": "Sent during the last 3/1 trial days.",
     "htmlBody": _wrap(
         "<h1>Trial ending soon</h1><p>{{userName}}, your free trial ends "
         "in <strong>{{daysLeft}}</strong> days. Upgrade to keep your "
         "content engine running.</p>"
         "<p><a href='{{upgradeUrl}}'>Choose a plan →</a></p>")},
    {"key": "site_connected", "name": "Site Connected",
     "category": "INTEGRATION",
     "subject": "🎉 {{siteName}} is now connected!",
     "variables": ["userName", "siteName", "siteUrl", "dashboardUrl"],
     "description": "Sent when WordPress plugin connects successfully.",
     "htmlBody": _wrap(
         "<h1 style='margin:0 0 12px'>Great news, {{userName}}!</h1>"
         "<p>Your website <strong>{{siteName}}</strong> is now connected "
         "to SEO Jalwa. We'll start publishing articles automatically "
         "based on your settings.</p>"
         "<p><a href='{{dashboardUrl}}' style='background:#16a34a;"
         "color:#fff;padding:12px 20px;border-radius:8px;"
         "text-decoration:none;display:inline-block'>"
         "View dashboard →</a></p>")},
    {"key": "announcement", "name": "Admin Announcement",
     "category": "MARKETING",
     "subject": "{{subject}}",
     "variables": ["subject", "message"],
     "description": "Used by `POST /api/admin/announcements`.",
     "htmlBody": _wrap("<h1>{{subject}}</h1><div>{{message}}</div>")},
]


async def seed_templates() -> int:
    """Idempotently insert any seed template that isn't already in DB."""
    db = get_db()
    inserted = 0
    for t in SEED_TEMPLATES:
        existing = await db.email_templates.find_one({"key": t["key"]})
        if existing:
            continue
        doc = dict(t)
        doc.update({"isActive": True,
                    "createdAt": utcnow_iso(),
                    "updatedAt": utcnow_iso()})
        await db.email_templates.insert_one(doc)
        inserted += 1
    return inserted


# Tiny TTL cache (60 s) — admin edits propagate in ≤ 1 min.
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 60.0


def _cache_get(key: str) -> Optional[dict]:
    item = _CACHE.get(key)
    if item and item[0] > time.time():
        return item[1]
    return None


def _cache_set(key: str, doc: dict) -> None:
    _CACHE[key] = (time.time() + _TTL, doc)


def invalidate_cache(key: Optional[str] = None) -> None:
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)


async def get_template(key: str) -> Optional[dict]:
    cached = _cache_get(key)
    if cached:
        return cached
    doc = await get_db().email_templates.find_one(
        {"key": key, "isActive": True}, {"_id": 0})
    if doc:
        _cache_set(key, doc)
    return doc


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def render(text: str, vars: dict[str, Any]) -> str:
    return _VAR_RE.sub(
        lambda m: str(vars.get(m.group(1), m.group(0))), text or "")


async def render_template(key: str, vars: dict[str, Any]) -> Optional[dict]:
    """Returns `{subject, html}` ready to send, or None if template missing."""
    tpl = await get_template(key)
    if not tpl:
        return None
    return {
        "subject": render(tpl.get("subject", ""), vars),
        "html": render(tpl.get("htmlBody", ""), vars),
    }
