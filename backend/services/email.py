"""Email service — SendGrid (primary) + Resend (fallback).

Provider selection logic (in order):
  1. SendGrid if `sendgrid.api_key` is configured.
  2. Resend if `resend.api_key` is configured and SendGrid is not.
  3. Skip with a warning if neither is configured.

Every send is fault-tolerant: a missing key or provider error is logged
and returned as `{success:false}` — the calling endpoint never raises.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

logger = logging.getLogger("jalwa.email")

FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "hello@seojalwa.com")
FROM_NAME = os.environ.get("SENDGRID_FROM_NAME", "SEO Jalwa")


async def _send_via_sendgrid(to: str, subject: str, html: str,
                             text: Optional[str], template: str,
                             api_key: str, from_email: str) -> dict:
    try:
        message = Mail(
            from_email=(from_email, FROM_NAME),
            to_emails=to,
            subject=subject,
            html_content=html,
            plain_text_content=text,
        )
        response = SendGridAPIClient(api_key=api_key).send(message)
        logger.info("[EMAIL sent via sendgrid] template=%s to=%s status=%s",
                    template, to, response.status_code)
        return {"success": True, "provider": "sendgrid",
                "status_code": response.status_code,
                "to": to, "template": template}
    except Exception as e:
        logger.exception("SendGrid send failed: %s", e)
        return {"success": False, "provider": "sendgrid",
                "error": str(e), "to": to, "template": template}


async def _send_via_resend(to: str, subject: str, html: str,
                           text: Optional[str], template: str,
                           api_key: str, from_email: str) -> dict:
    payload: dict = {
        "from": f"{FROM_NAME} <{from_email}>",
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload)
        logger.info("[EMAIL sent via resend] template=%s to=%s status=%s",
                    template, to, r.status_code)
        if r.status_code in (200, 201, 202):
            return {"success": True, "provider": "resend",
                    "status_code": r.status_code,
                    "to": to, "template": template}
        return {"success": False, "provider": "resend",
                "status_code": r.status_code,
                "error": r.text[:200], "to": to, "template": template}
    except Exception as e:
        logger.exception("Resend send failed: %s", e)
        return {"success": False, "provider": "resend",
                "error": str(e), "to": to, "template": template}


async def _write_log(provider: str, to: str, subject: str,
                     template: str, status: str, error: str = "",
                     status_code: int | None = None,
                     user_id: str | None = None) -> None:
    """Best-effort write into the `email_logs` collection."""
    import uuid as _uuid
    try:
        from core.database import get_db
        await get_db().email_logs.insert_one({
            "id": str(_uuid.uuid4()),
            "userId": user_id,
            "to": to,
            "subject": subject,
            "templateKey": template,
            "status": status.upper(),
            "provider": provider.upper() if provider else None,
            "statusCode": status_code,
            "errorMessage": error or None,
            "sentAt": utcnow_iso(),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("email_logs write failed: %s", e)


def utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- low-level
async def send_email(
    to: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
    template: str = "generic",
    user_id: Optional[str] = None,
) -> dict:
    """Send email via the first configured provider (SendGrid > Resend).

    Never raises. Returns `{success, provider?, status_code?, error?}`.
    Also persists a row in the `email_logs` collection.
    """
    from services.config import config_service

    # 1) SendGrid (preferred)
    sg_fields = await config_service.get_fields("sendgrid")
    sg_key = sg_fields.get("api_key") or os.environ.get("SENDGRID_API_KEY")
    if sg_key:
        sg_from = (sg_fields.get("from_email")
                   or os.environ.get("SENDGRID_FROM_EMAIL", FROM_EMAIL))
        result = await _send_via_sendgrid(
            to, subject, html, text, template, sg_key, sg_from)
        await _write_log("sendgrid", to, subject, template,
                          "SENT" if result.get("success") else "FAILED",
                          error=str(result.get("error", "")),
                          status_code=result.get("status_code"),
                          user_id=user_id)
        return result

    # 2) Resend (fallback)
    re_fields = await config_service.get_fields("resend")
    re_key = re_fields.get("api_key") or os.environ.get("RESEND_API_KEY")
    if re_key:
        re_from = (re_fields.get("from_email")
                   or os.environ.get("RESEND_FROM_EMAIL", FROM_EMAIL))
        result = await _send_via_resend(
            to, subject, html, text, template, re_key, re_from)
        await _write_log("resend", to, subject, template,
                          "SENT" if result.get("success") else "FAILED",
                          error=str(result.get("error", "")),
                          status_code=result.get("status_code"),
                          user_id=user_id)
        return result

    # 3) Neither configured
    logger.warning(
        "Email not sent to %s (template=%s): No email provider configured. "
        "Add SendGrid or Resend API key in admin panel.",
        to, template)
    await _write_log("", to, subject, template, "SKIPPED",
                      error="no_email_provider_configured",
                      user_id=user_id)
    return {"success": False, "skipped": True, "to": to,
            "template": template,
            "error": "no_email_provider_configured"}


# ---------------------------------------------------------------- shell HTML
def _shell(title: str, body_html: str) -> str:
    """Common HTML wrapper for all templates."""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#f4f6fb;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" bgcolor="#f4f6fb">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(15,23,42,0.06);">
<tr><td style="padding:24px 32px;border-bottom:1px solid #e5e7eb;">
<div style="font-size:20px;font-weight:700;color:#0f172a;">SEO Jalwa</div>
</td></tr>
<tr><td style="padding:32px;">{body_html}</td></tr>
<tr><td style="padding:20px 32px;background:#f8fafc;border-top:1px solid #e5e7eb;font-size:12px;color:#64748b;">
SEO Jalwa · hello@seojalwa.com · <a href="https://seojalwa.com/unsubscribe" style="color:#64748b;">Unsubscribe</a>
</td></tr>
</table></td></tr></table></body></html>"""


def _btn(label: str, url: str, colour: str = "#16a34a") -> str:
    return (f'<a href="{url}" style="display:inline-block;background:{colour};'
            f'color:#ffffff;font-weight:600;text-decoration:none;padding:12px 22px;'
            f'border-radius:8px;">{label}</a>')


# ============================ Templates ===============================

async def welcome_email(user_name: str, to: str, login_url: str) -> dict:
    body = f"""
<h1 style="margin:0 0 12px;font-size:24px;color:#0f172a;">Welcome {user_name}! 🚀</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#334155;">
Thanks for signing up for SEO Jalwa. Here are three quick steps to get you up and running:</p>
<ol style="font-size:15px;line-height:1.7;color:#334155;padding-left:20px;margin:0 0 24px;">
  <li>Connect your first site (WordPress, Webflow, Ghost or other).</li>
  <li>Add a few seed keywords or let our AI suggest topics.</li>
  <li>Turn on Auto-publish and watch articles ship daily.</li>
</ol>
<p style="margin:0 0 24px;">{_btn("Go to Dashboard", login_url)}</p>
<p style="font-size:13px;color:#64748b;margin:0;">Questions? Just reply to this email.</p>"""
    return await send_email(to, "Welcome to SEO Jalwa 🚀",
                            _shell("Welcome", body), template="welcome")


async def verify_email(user_name: str, to: str, verify_url: str) -> dict:
    body = f"""
<h1 style="margin:0 0 12px;font-size:22px;">Verify Your Email</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#334155;">
Hi {user_name}, please confirm this is your email address so we can secure your account.</p>
<p style="margin:0 0 24px;">{_btn("Verify Email", verify_url)}</p>
<p style="font-size:13px;color:#64748b;margin:0;">This link expires in 24 hours.</p>"""
    return await send_email(to, "Verify your email — SEO Jalwa",
                            _shell("Verify your email", body),
                            template="verify-email")


async def password_reset(user_name: str, to: str, reset_url: str) -> dict:
    body = f"""
<h1 style="margin:0 0 12px;font-size:22px;">Reset Your Password</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#334155;">
Hi {user_name}, click the button below to choose a new password.</p>
<p style="margin:0 0 24px;">{_btn("Reset Password", reset_url, "#0f172a")}</p>
<p style="font-size:13px;color:#64748b;margin:0;">This link expires in 1 hour. If you didn't request this, ignore this email.</p>"""
    return await send_email(to, "Reset your password",
                            _shell("Reset your password", body),
                            template="password-reset")


async def article_published(user_name: str, to: str, article_title: str,
                             article_url: str, site_url: str,
                             seo_score: int, dashboard_url: str) -> dict:
    body = f"""
<h1 style="margin:0 0 12px;font-size:22px;">New article published ✅</h1>
<p style="margin:0 0 8px;font-size:13px;color:#64748b;">Hi {user_name},</p>
<p style="margin:0 0 8px;font-size:17px;font-weight:600;color:#0f172a;">{article_title}</p>
<p style="margin:0 0 16px;font-size:14px;"><a href="{article_url}" style="color:#16a34a;">View on {site_url}</a></p>
<div style="display:inline-block;padding:10px 16px;background:#ecfdf5;border:1px solid #6ee7b7;border-radius:999px;color:#065f46;font-weight:600;font-size:14px;margin-bottom:24px;">
  SEO Score: {seo_score}/100
</div>
<p style="margin:0;">{_btn("View in Dashboard", dashboard_url)}</p>"""
    return await send_email(to, "New article published ✅",
                            _shell("New article published", body),
                            template="article-published")


async def weekly_digest(user_name: str, to: str, growth_score: int,
                         score_change: int, articles_published: int,
                         top_article_title: str, top_article_clicks: int,
                         report_url: str) -> dict:
    change_str = f"+{score_change}" if score_change >= 0 else str(score_change)
    body = f"""
<h1 style="margin:0 0 12px;font-size:22px;">Your weekly SEO report 📊</h1>
<p style="margin:0 0 16px;font-size:15px;color:#334155;">Hi {user_name}, here is how your sites performed this week.</p>
<table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
  <tr><td style="padding:10px 12px;background:#f1f5f9;border-radius:8px;font-size:13px;color:#475569;">Growth Score</td>
      <td style="padding:10px 12px;text-align:right;font-size:18px;font-weight:700;color:#0f172a;">{growth_score} <span style="font-size:13px;color:#16a34a;">({change_str})</span></td></tr>
  <tr><td style="padding:10px 12px;font-size:13px;color:#475569;">Articles published</td>
      <td style="padding:10px 12px;text-align:right;font-size:16px;color:#0f172a;">{articles_published}</td></tr>
  <tr><td style="padding:10px 12px;background:#f1f5f9;border-radius:8px;font-size:13px;color:#475569;">Top performer</td>
      <td style="padding:10px 12px;text-align:right;font-size:13px;color:#0f172a;"><b>{top_article_title}</b><br><span style="color:#64748b;font-size:12px;">{top_article_clicks} clicks</span></td></tr>
</table>
<p style="margin:0;">{_btn("View Full Report", report_url)}</p>"""
    return await send_email(to, f"Your weekly SEO report — Growth Score: {growth_score}",
                            _shell("Weekly digest", body),
                            template="weekly-digest")


async def team_invite(inviter_name: str, workspace_name: str, to: str,
                       accept_url: str) -> dict:
    body = f"""
<h1 style="margin:0 0 12px;font-size:22px;">You've been invited 🎉</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#334155;">
<b>{inviter_name}</b> has invited you to join <b>{workspace_name}</b> on SEO Jalwa.</p>
<p style="margin:0 0 24px;">{_btn("Accept Invitation", accept_url)}</p>
<p style="font-size:13px;color:#64748b;margin:0;">This invite expires in 7 days.</p>"""
    return await send_email(to, f"{inviter_name} invited you to SEO Jalwa",
                            _shell("Team invite", body),
                            template="team-invite")


async def announcement_email(to: str, subject: str, message_html: str) -> dict:
    body = f"""
<h1 style="margin:0 0 16px;font-size:22px;">{subject}</h1>
<div style="font-size:15px;line-height:1.7;color:#334155;">{message_html}</div>"""
    return await send_email(to, subject, _shell(subject, body),
                            template="announcement")


async def payment_failed(user_name: str, to: str, billing_url: str) -> dict:
    body = f"""
<h1 style="margin:0 0 12px;font-size:22px;">Payment failed — action required</h1>
<p style="margin:0 0 16px;font-size:15px;color:#334155;">
Hi {user_name}, we couldn't charge your card for your latest SEO Jalwa invoice.</p>
<p style="margin:0 0 24px;">{_btn("Update payment method", billing_url, "#dc2626")}</p>"""
    return await send_email(to, "Payment failed — action required",
                            _shell("Payment failed", body),
                            template="payment-failed")


async def test_sendgrid() -> dict:
    """Used by admin api-keys/test."""
    from services.config import config_service
    fields = await config_service.get_fields("sendgrid")
    if not (fields.get("api_key") or os.environ.get("SENDGRID_API_KEY")):
        return {"success": False, "message": "SENDGRID_API_KEY not configured"}
    res = await send_email(
        fields.get("from_email") or FROM_EMAIL,
        "SEO Jalwa SendGrid health check",
        "<p>If you can read this, SendGrid is wired correctly.</p>",
        template="health-check")
    return res
