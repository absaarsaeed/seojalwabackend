"""Admin: GPT-4o retention insights (24h cached)."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import ok
from services.llm import chat_completion

logger = logging.getLogger("jalwa.insights")

router = APIRouter(prefix="/admin/insights", tags=["admin-insights"],
                   dependencies=[Depends(get_admin_session)])

_CACHE: dict = {"expires": 0, "data": None, "metrics": None}
_TTL = 24 * 3600


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _gather_metrics() -> dict:
    db = get_db()
    d30 = _iso_days_ago(30)
    d7 = _iso_days_ago(7)
    d14 = _iso_days_ago(14)

    total_users = await db.users.count_documents({})
    active_users = await db.user_activity_log.count_documents(
        {"action": "USER_LOGGED_IN", "createdAt": {"$gte": d7}})
    inactive_users = await db.users.count_documents(
        {"$or": [{"lastLoginAt": {"$lt": d14}},
                 {"lastLoginAt": {"$exists": False},
                  "createdAt": {"$lt": d14}}]})

    trial_expiring = await db.subscriptions.count_documents(
        {"status": "TRIALING",
         "trialEndsAt": {"$lt": _iso_days_ago(-3),
                          "$gte": datetime.now(timezone.utc).isoformat()}})

    users_with_articles = await db.articles.distinct(
        "userId", {"createdAt": {"$gte": d30}})
    no_article_users = max(0, total_users - len(users_with_articles or []))

    low_growth_count = await db.growth_scores.count_documents(
        {"score": {"$lt": 40}, "createdAt": {"$gte": d30}})
    failed_articles = await db.articles.count_documents(
        {"status": "FAILED", "createdAt": {"$gte": d30}})
    cancellations = await db.subscriptions.count_documents(
        {"status": "CANCELLED", "updatedAt": {"$gte": d30}})

    active_subs = await db.subscriptions.count_documents(
        {"status": {"$in": ["ACTIVE", "TRIALING"]}})
    churn_rate = round((cancellations / active_subs * 100), 2) \
        if active_subs else 0.0

    return {
        "totalUsers": total_users,
        "activeUsersLast7Days": active_users,
        "inactiveUsers14DaysPlus": inactive_users,
        "trialsExpiringIn3Days": trial_expiring,
        "usersWithoutArticlesLast30Days": no_article_users,
        "sitesWithLowGrowthScore": low_growth_count,
        "failedArticleGenerationsLast30Days": failed_articles,
        "cancellationsLast30Days": cancellations,
        "churnRatePercent": churn_rate,
    }


SYSTEM_PROMPT = (
    "You are an expert SaaS growth analyst. Based on these SEO Jalwa "
    "platform metrics, suggest 5–10 specific actionable improvements to "
    "increase user retention and reduce churn. Focus on practical "
    "actions a small team can ship in a week or less. Return ONLY a JSON "
    "array, no preamble, no markdown fences. Each item must be a single "
    "object with these exact keys: priority (high|medium|low), category "
    "(onboarding|engagement|feature|communication|pricing|support), title "
    "(short), insight (what you noticed in the data), recommendation "
    "(what to do), expectedImpact (1-line description), effort "
    "(low|medium|high)."
)


@router.get("/retention")
async def retention_insights(force: bool = False):
    now = time.time()
    if not force and _CACHE["data"] and _CACHE["expires"] > now:
        return ok({"suggestions": _CACHE["data"],
                   "metrics": _CACHE["metrics"],
                   "cachedAt": _CACHE["expires"] - _TTL, "cached": True})

    metrics = await _gather_metrics()
    user_prompt = (
        f"Platform metrics (last 30 days):\n{json.dumps(metrics, indent=2)}"
    )

    raw = ""
    try:
        raw = await chat_completion(SYSTEM_PROMPT, user_prompt,
                                     model="gpt-4o")
    except Exception as e:
        logger.exception("retention insights llm error: %s", e)
        return ok({"suggestions": [], "metrics": metrics,
                   "error": str(e)[:200], "cached": False})

    suggestions: list = []
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip("`\n ")
        suggestions = json.loads(cleaned)
        if not isinstance(suggestions, list):
            suggestions = []
    except Exception:
        logger.warning("could not parse LLM output: %s", raw[:200])
        suggestions = []

    _CACHE["data"] = suggestions
    _CACHE["metrics"] = metrics
    _CACHE["expires"] = now + _TTL

    return ok({"suggestions": suggestions, "metrics": metrics,
               "cached": False})
