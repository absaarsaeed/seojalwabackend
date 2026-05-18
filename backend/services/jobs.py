"""Background job runners (FastAPI BackgroundTasks + APScheduler crons).

Each function is awaitable and writes results back to MongoDB.  Cron entry
points just iterate over eligible records and call the per-record worker.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta

from core.database import get_db
from core.security import utcnow_iso
from services import llm, mocks

logger = logging.getLogger("jalwa.jobs")


async def _update_job(job_id: str, **fields):
    fields["updatedAt"] = utcnow_iso()
    await get_db().jobs.update_one({"id": job_id}, {"$set": fields})


async def _create_job(job_type: str, payload: dict) -> str:
    db = get_db()
    job_id = str(uuid.uuid4())
    await db.jobs.insert_one({
        "id": job_id, "type": job_type, "status": "queued",
        "progress": 0, "payload": payload,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    })
    return job_id


# ---------------------------------------------------------------- article gen
async def run_article_generation(job_id: str, article_id: str, site_id: str,
                                 user_id: str, search_term: str):
    db = get_db()
    await _update_job(job_id, status="running", progress=10)
    try:
        settings = await db.article_settings.find_one(
            {"siteId": site_id}, {"_id": 0}) or {}
        brand_voice = await db.brand_voices.find_one(
            {"siteId": site_id, "isActive": True}, {"_id": 0})
        length_map = {"WORDS_1000": 1000, "WORDS_2000": 2000,
                      "WORDS_3000": 3000, "WORDS_5000": 5000}
        length = length_map.get(settings.get("articleLength", "WORDS_2000"), 2000)

        await _update_job(job_id, progress=30)
        gen = await llm.generate_article(
            topic=search_term, keyword=search_term,
            brand_voice=brand_voice, length_words=length,
            instructions=settings.get("writingInstructions", ""),
        )

        await _update_job(job_id, progress=70)
        image_url = await llm.generate_image(f"Hero illustration for: {search_term}")
        seo_score = 60 + (abs(hash(search_term)) % 35)
        slug = "-".join(search_term.lower().split())[:100]

        await db.articles.update_one(
            {"id": article_id},
            {"$set": {
                "title": gen["title"], "content": gen["content"],
                "slug": slug, "wordCount": gen["wordCount"],
                "featuredImageUrl": image_url, "seoScore": seo_score,
                "status": "DRAFT", "updatedAt": utcnow_iso(),
            }},
        )
        await _update_job(job_id, status="completed", progress=100,
                          result={"articleId": article_id})

        # Auto-publish if enabled and CMS connected
        if settings.get("autoPublish", True):
            site = await db.sites.find_one({"id": site_id}, {"_id": 0})
            if site and site.get("wordpressConnected"):
                await db.articles.update_one(
                    {"id": article_id},
                    {"$set": {"status": "SCHEDULED",
                              "scheduledAt": utcnow_iso()}})

    except Exception as e:
        logger.exception("article gen failed")
        await _update_job(job_id, status="failed", error=str(e))
        await db.articles.update_one(
            {"id": article_id}, {"$set": {"status": "FAILED"}})


# ---------------------------------------------------------- social post gen
async def run_social_post_generation(job_id: str, article_id: str,
                                     user_id: str, site_id: str,
                                     platforms: list[str] | None = None):
    db = get_db()
    await _update_job(job_id, status="running", progress=20)
    try:
        article = await db.articles.find_one({"id": article_id}, {"_id": 0})
        if not article:
            raise RuntimeError("article not found")
        brand_voice = await db.brand_voices.find_one(
            {"siteId": site_id, "isActive": True}, {"_id": 0})
        if platforms is None:
            accounts = await db.social_accounts.find(
                {"userId": user_id, "isActive": True}, {"_id": 0}).to_list(50)
            platforms = list({a["platform"] for a in accounts})

        created = []
        for plat in platforms:
            data = await llm.generate_social_caption(article["title"], plat,
                                                     brand_voice)
            img = await llm.generate_image(
                f"Social image ({plat}) for {article['title']}")
            post = {
                "id": str(uuid.uuid4()), "userId": user_id, "siteId": site_id,
                "articleId": article_id, "platform": plat,
                "caption": data["caption"], "imageUrl": img,
                "hashtags": data["hashtags"], "status": "PENDING_APPROVAL",
                "scheduledAt": (datetime.now(timezone.utc)
                                + timedelta(hours=2)).isoformat(),
                "reach": 0, "likes": 0, "clicks": 0,
                "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
            }
            await db.social_posts.insert_one(dict(post))
            post.pop("_id", None)
            created.append(post)

        await _update_job(job_id, status="completed", progress=100,
                          result={"posts": [p["id"] for p in created]})
    except Exception as e:
        logger.exception("social gen failed")
        await _update_job(job_id, status="failed", error=str(e))


# ----------------------------------------------------------- ai visibility
async def run_ai_visibility_scan(job_id: str, site_id: str, user_id: str):
    db = get_db()
    await _update_job(job_id, status="running", progress=20)
    try:
        site = await db.sites.find_one({"id": site_id}, {"_id": 0}) or {}
        brand = site.get("name") or site.get("url") or "brand"
        queries = [f"What is {brand}?", f"Best alternatives to {brand}",
                   f"{brand} reviews"]
        results = await mocks.query_ai_models(brand, queries)
        overall = sum(r["score"] for r in results.values()) // max(len(results), 1)

        scan = {
            "id": str(uuid.uuid4()), "userId": user_id, "siteId": site_id,
            "overallScore": overall,
            "chatgptScore": results["chatgpt"]["score"],
            "perplexityScore": results["perplexity"]["score"],
            "geminiScore": results["gemini"]["score"],
            "claudeScore": results["claude"]["score"],
            "copilotScore": results["copilot"]["score"],
            "chatgptSentiment": results["chatgpt"]["sentiment"],
            "perplexitySentiment": results["perplexity"]["sentiment"],
            "geminiSentiment": results["gemini"]["sentiment"],
            "claudeSentiment": results["claude"]["sentiment"],
            "copilotSentiment": results["copilot"]["sentiment"],
            "recommendations": [
                "Improve schema markup on key landing pages",
                "Publish more in-depth comparison articles",
                "Earn citations from authoritative sources",
            ],
            "rawResults": results,
            "scannedAt": utcnow_iso(), "createdAt": utcnow_iso(),
        }
        await db.ai_visibility_scans.insert_one(dict(scan))
        scan.pop("_id", None)
        await _update_job(job_id, status="completed", progress=100,
                          result={"scanId": scan["id"], "overallScore": overall})
    except Exception as e:
        logger.exception("ai visibility scan failed")
        await _update_job(job_id, status="failed", error=str(e))


# -------------------------------------------------------------- growth score
async def run_growth_score(site_id: str, user_id: str) -> dict:
    db = get_db()
    last_scan = await db.ai_visibility_scans.find_one(
        {"siteId": site_id}, {"_id": 0}, sort=[("scannedAt", -1)])
    ai_comp = (last_scan or {}).get("overallScore", 50)

    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    articles_30 = await db.articles.count_documents(
        {"siteId": site_id, "publishedAt": {"$gte": month_ago}})
    seo_comp = min(articles_30 * 10, 100)

    posts_30 = await db.social_posts.count_documents(
        {"siteId": site_id, "publishedAt": {"$gte": month_ago}})
    social_comp = min(posts_30 * 5, 100)

    traffic_comp = 60  # placeholder until real GSC traffic trend wired

    score = int(round(
        ai_comp * 0.30 + seo_comp * 0.25 + social_comp * 0.25 + traffic_comp * 0.20
    ))
    record = {
        "id": str(uuid.uuid4()), "userId": user_id, "siteId": site_id,
        "score": score, "aiVisibilityComponent": ai_comp,
        "seoContentComponent": seo_comp,
        "socialConsistencyComponent": social_comp,
        "trafficTrendComponent": traffic_comp,
        "calculatedAt": utcnow_iso(),
    }
    await db.growth_scores.insert_one(dict(record))
    record.pop("_id", None)
    return record


# -------------------------------------------------------------- GSC sync
async def run_gsc_sync(site_id: str, user_id: str) -> dict:
    db = get_db()
    site = await db.sites.find_one({"id": site_id}, {"_id": 0}) or {}
    data = await mocks.gsc_fetch_performance(site.get("url", ""))
    await db.gsc_snapshots.insert_one({
        "id": str(uuid.uuid4()), "siteId": site_id, "userId": user_id,
        "data": data, "syncedAt": utcnow_iso(),
    })
    return {"synced": True, "lastSync": utcnow_iso(),
            "totalClicks": data["totalClicks"]}


# -------------------------------------------------------------- brand voice
async def run_brand_voice_training(job_id: str, site_id: str, user_id: str,
                                   samples: list[str]):
    db = get_db()
    await _update_job(job_id, status="running", progress=30)
    try:
        profile = await llm.analyse_brand_voice(samples)
        existing = await db.brand_voices.find_one(
            {"siteId": site_id}, {"_id": 0})
        doc = {
            "siteId": site_id, "userId": user_id,
            "styleProfile": profile,
            "formalityScore": int(profile.get("formalityScore", 50)),
            "playfulnessScore": int(profile.get("playfulnessScore", 50)),
            "technicalityScore": int(profile.get("technicalityScore", 50)),
            "trainedAt": utcnow_iso(), "isActive": True,
            "updatedAt": utcnow_iso(),
        }
        if existing:
            await db.brand_voices.update_one(
                {"id": existing["id"]}, {"$set": doc})
            result_id = existing["id"]
        else:
            doc["id"] = str(uuid.uuid4())
            doc["createdAt"] = utcnow_iso()
            await db.brand_voices.insert_one(dict(doc))
            result_id = doc["id"]
        await _update_job(job_id, status="completed", progress=100,
                          result={"brandVoiceId": result_id})
    except Exception as e:
        logger.exception("brand voice training failed")
        await _update_job(job_id, status="failed", error=str(e))


# =========================================================================
# CRON entry points
# =========================================================================
async def cron_daily_article_generation():
    db = get_db()
    logger.info("[CRON] daily article generation")
    cursor = db.article_settings.find({"autoPublish": True}, {"_id": 0})
    async for setting in cursor:
        site_id = setting["siteId"]
        user_id = setting["userId"]
        pending = await db.search_terms.find_one(
            {"siteId": site_id, "status": "PENDING"}, {"_id": 0})
        if not pending:
            continue
        article_id = str(uuid.uuid4())
        await db.articles.insert_one({
            "id": article_id, "siteId": site_id, "userId": user_id,
            "title": pending["term"], "slug": "",
            "content": "", "searchTerm": pending["term"],
            "status": "DRAFT", "wordCount": 0,
            "impressions": 0, "clicks": 0,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        })
        await db.search_terms.update_one(
            {"id": pending["id"]}, {"$set": {"status": "USED"}})
        job_id = await _create_job("article_generation",
                                   {"articleId": article_id})
        await run_article_generation(job_id, article_id, site_id, user_id,
                                     pending["term"])


async def cron_weekly_ai_visibility():
    db = get_db()
    logger.info("[CRON] weekly AI visibility")
    async for sub in db.subscriptions.find(
            {"status": "ACTIVE"}, {"_id": 0}):
        async for site in db.sites.find(
                {"userId": sub["userId"], "deleted": {"$ne": True}},
                {"_id": 0}):
            job_id = await _create_job("ai_visibility_scan",
                                       {"siteId": site["id"]})
            await run_ai_visibility_scan(job_id, site["id"], sub["userId"])


async def cron_weekly_growth_score():
    db = get_db()
    logger.info("[CRON] weekly growth score")
    async for site in db.sites.find(
            {"deleted": {"$ne": True}}, {"_id": 0}):
        await run_growth_score(site["id"], site["userId"])


async def cron_daily_gsc_sync():
    db = get_db()
    logger.info("[CRON] daily GSC sync")
    async for site in db.sites.find(
            {"deleted": {"$ne": True}}, {"_id": 0}):
        await run_gsc_sync(site["id"], site["userId"])


async def cron_hourly_social_publish():
    db = get_db()
    logger.info("[CRON] hourly social publish")
    now = datetime.now(timezone.utc).isoformat()
    async for post in db.social_posts.find(
            {"status": "SCHEDULED", "scheduledAt": {"$lte": now}},
            {"_id": 0}):
        account = await db.social_accounts.find_one(
            {"userId": post["userId"], "platform": post["platform"],
             "isActive": True}, {"_id": 0})
        if not account:
            continue
        try:
            res = await mocks.publish_social_post(post["platform"], account, post)
            await db.social_posts.update_one(
                {"id": post["id"]},
                {"$set": {"status": "PUBLISHED",
                          "publishedAt": utcnow_iso(),
                          "platformPostId": res["platformPostId"]}})
        except Exception as e:
            await db.social_posts.update_one(
                {"id": post["id"]},
                {"$set": {"status": "FAILED", "error": str(e)}})


# Expose helpers for routers
create_job = _create_job
update_job = _update_job
