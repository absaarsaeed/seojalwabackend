"""Background job runners (FastAPI BackgroundTasks + APScheduler crons).

Each function is awaitable and writes results back to MongoDB.  Cron entry
points just iterate over eligible records and call the per-record worker.
"""
import logging
import os
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
        # 20 — brand voice lookup
        await _update_job(job_id, progress=20)
        brand_voice = await db.brand_voices.find_one(
            {"siteId": site_id, "isActive": True}, {"_id": 0})
        length_map = {"WORDS_1000": 1000, "WORDS_2000": 2000,
                      "WORDS_3000": 3000, "WORDS_5000": 5000}
        length = length_map.get(settings.get("articleLength", "WORDS_2000"), 2000)

        # 30 — keyword research stage marker
        await _update_job(job_id, progress=30)

        # 50 — generating article content
        await _update_job(job_id, progress=50)
        gen = await llm.generate_article(
            topic=search_term, keyword=search_term,
            brand_voice=brand_voice, length_words=length,
            instructions=settings.get("writingInstructions", ""),
            language=settings.get("writingLanguage", "English"),
            include_hero_image=settings.get("includeHeroImages", True),
            include_toc=settings.get("includeTableOfContents", True),
            include_key_takeaways=settings.get("includeKeyTakeaways", True),
            imagery_prompt=settings.get("imageryPrompt", "") or "",
        )

        # Real DALL-E 3 hero (if enabled) + re-upload to R2
        image_url = None
        if settings.get("includeHeroImages", True):
            await _update_job(job_id, progress=70)  # generating hero image
            openai_img = await llm.generate_hero_image(
                gen["title"], settings.get("imageryPrompt", ""))
            if openai_img:
                await _update_job(job_id, progress=85)  # uploading to storage
                from services import storage as _storage
                image_url = await _storage.download_to_r2(
                    openai_img, f"articles/{article_id}/hero.jpg",
                    content_type="image/jpeg")

        slug = "-".join(gen["title"].lower().split())[:100] or article_id[:8]
        await db.articles.update_one(
            {"id": article_id},
            {"$set": {
                "title": gen["title"],
                "metaTitle": gen.get("metaTitle") or gen["title"][:60],
                "metaDescription": gen.get("metaDescription", ""),
                "excerpt": gen.get("excerpt", ""),
                "content": gen["content"],
                "slug": slug,
                "wordCount": gen["wordCount"],
                "estimatedReadTime": gen.get("estimatedReadTime", 0),
                "keyTakeaways": gen.get("keyTakeaways", []),
                "faqSchema": gen.get("faqSchema", []),
                "suggestedTags": gen.get("suggestedTags", []),
                "featuredImageUrl": image_url,
                "seoScore": gen["seoScore"],
                "status": "DRAFT", "updatedAt": utcnow_iso(),
            }},
        )
        await _update_job(job_id, status="completed", progress=100,
                          result={"articleId": article_id,
                                  "seoScore": gen["seoScore"]})

        # Auto-publish if enabled and CMS connected
        if settings.get("autoPublish", True):
            site = await db.sites.find_one({"id": site_id}, {"_id": 0})
            if site and site.get("wordpressConnected"):
                await _update_job(job_id, progress=95)  # publishing to WP
                # Real WordPress publish via REST API
                from services import wordpress as _wp, email as _em
                article = await db.articles.find_one({"id": article_id},
                                                     {"_id": 0})
                res = await _wp.publish_article(site, article or {})
                if res.get("success"):
                    await db.articles.update_one(
                        {"id": article_id},
                        {"$set": {
                            "status": "PUBLISHED",
                            "publishedAt": utcnow_iso(),
                            "cmsPostId": res.get("cmsPostId"),
                            "cmsUrl": res.get("cmsUrl"),
                            "updatedAt": utcnow_iso(),
                        }})
                    user = await db.users.find_one(
                        {"id": user_id}, {"_id": 0, "password": 0})
                    if user:
                        await _em.article_published(
                            user.get("fullName", "there"),
                            user["email"], gen["title"],
                            res.get("cmsUrl", ""), site.get("url", ""),
                            gen["seoScore"],
                            f"{os.environ.get('FRONTEND_URL', '')}/dashboard")
                else:
                    # No WP token yet — leave SCHEDULED for plugin to pull
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
        from services import ai_visibility as _av
        site = await db.sites.find_one({"id": site_id}, {"_id": 0}) or {}

        await _update_job(job_id, progress=40)
        scan_data = await _av.run_scan(site)

        scan = {
            "id": str(uuid.uuid4()), "userId": user_id, "siteId": site_id,
            **scan_data,
            "scannedAt": utcnow_iso(), "createdAt": utcnow_iso(),
        }
        await db.ai_visibility_scans.insert_one(dict(scan))

        # Recompute growth score now that we have fresh AI signals
        await run_growth_score(site_id, user_id)

        await _update_job(job_id, status="completed", progress=100,
                          result={"scanId": scan["id"],
                                  "overallScore": scan_data["overallScore"]})
    except Exception as e:
        logger.exception("ai visibility scan failed")
        await _update_job(job_id, status="failed", error=str(e))


# -------------------------------------------------------------- growth score
async def run_growth_score(site_id: str, user_id: str) -> dict:
    """Real Growth Score: AI 30%, SEO 25%, Social 25%, Traffic 20%."""
    db = get_db()

    # --- AI Visibility (30%) ---
    last_scan = await db.ai_visibility_scans.find_one(
        {"siteId": site_id}, {"_id": 0}, sort=[("scannedAt", -1)])
    ai_score = (last_scan or {}).get("overallScore", 0)
    ai_component = ai_score * 0.30

    # --- SEO content (25%) ---
    settings = await db.article_settings.find_one(
        {"siteId": site_id}, {"_id": 0}) or {}
    target_per_month = max(int(settings.get("publishingFrequency", 5)) * 4, 1)
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    articles_30 = await db.articles.count_documents({
        "siteId": site_id, "publishedAt": {"$gte": month_ago},
        "status": "PUBLISHED", "deleted": {"$ne": True},
    })
    content_ratio = min(articles_30 / target_per_month, 1.0)
    content_component = content_ratio * 100 * 0.25

    # --- Social Consistency (25%) ---
    posts_30 = await db.social_posts.count_documents({
        "siteId": site_id, "publishedAt": {"$gte": month_ago},
        "status": "PUBLISHED",
    })
    social_ratio = min(posts_30 / 20, 1.0)
    social_component = social_ratio * 100 * 0.25

    # --- Traffic Trend (20%) ---
    sixty_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    cur_pipeline = [
        {"$match": {"siteId": site_id,
                    "publishedAt": {"$gte": month_ago}}},
        {"$group": {"_id": None, "clicks": {"$sum": "$clicks"}}},
    ]
    prev_pipeline = [
        {"$match": {"siteId": site_id,
                    "publishedAt": {"$gte": sixty_ago, "$lt": month_ago}}},
        {"$group": {"_id": None, "clicks": {"$sum": "$clicks"}}},
    ]
    cur = await db.articles.aggregate(cur_pipeline).to_list(1)
    prev = await db.articles.aggregate(prev_pipeline).to_list(1)
    cur_clicks = cur[0]["clicks"] if cur else 0
    prev_clicks = prev[0]["clicks"] if prev else 0
    if prev_clicks > 0:
        trend = (cur_clicks - prev_clicks) / prev_clicks
        trend_score = min(max(50 + (trend * 50), 0), 100)
    else:
        trend_score = 50
    traffic_component = trend_score * 0.20

    score = int(round(
        ai_component + content_component + social_component + traffic_component
    ))

    record = {
        "id": str(uuid.uuid4()), "userId": user_id, "siteId": site_id,
        "score": score,
        "aiVisibilityComponent": int(round(ai_component / 0.30)) if True else ai_score,
        "seoContentComponent": int(round(content_component / 0.25)),
        "socialConsistencyComponent": int(round(social_component / 0.25)),
        "trafficTrendComponent": int(round(trend_score)),
        "calculatedAt": utcnow_iso(),
    }
    await db.growth_scores.insert_one(dict(record))
    record.pop("_id", None)
    return record


# -------------------------------------------------------------- GSC sync
async def run_gsc_sync(site_id: str, user_id: str) -> dict:
    """Real Google Search Console sync — pulls 30 days of query/page data
    and propagates clicks/impressions/CTR/position onto the matching Article
    records by URL."""
    db = get_db()
    site = await db.sites.find_one({"id": site_id}, {"_id": 0}) or {}
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        return {"synced": False, "reason": "user_not_found"}

    from core.encryption import decrypt
    access = decrypt(user.get("gscAccessToken")) if user.get(
        "gscAccessToken") else None
    refresh = decrypt(user.get("gscRefreshToken")) if user.get(
        "gscRefreshToken") else None
    if not access:
        return {"synced": False, "reason": "no_gsc_token"}

    from services import gsc as _gsc
    fetched = await _gsc.fetch_performance(site.get("url", ""), access, refresh)
    rows = fetched.get("rows", [])

    # Aggregate per page URL → update article counters
    page_agg: dict[str, dict] = {}
    for r in rows:
        keys = r.get("keys", ["", ""])
        page = keys[-1] if len(keys) > 1 else keys[0]
        d = page_agg.setdefault(page, {"clicks": 0, "impressions": 0,
                                       "position": 0.0, "n": 0})
        d["clicks"] += int(r.get("clicks", 0))
        d["impressions"] += int(r.get("impressions", 0))
        d["position"] += float(r.get("position", 0))
        d["n"] += 1

    updated = 0
    async for art in db.articles.find(
            {"siteId": site_id, "deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "cmsUrl": 1, "slug": 1}):
        match = next((p for p in page_agg
                      if art.get("cmsUrl") and p in art["cmsUrl"]
                      or (art.get("slug") and art["slug"] in p)), None)
        if not match:
            continue
        d = page_agg[match]
        ctr = (d["clicks"] / d["impressions"] * 100) if d["impressions"] else 0
        avg_pos = (d["position"] / d["n"]) if d["n"] else 0
        await db.articles.update_one(
            {"id": art["id"]},
            {"$set": {"clicks": d["clicks"], "impressions": d["impressions"],
                      "ctr": round(ctr, 2), "avgPosition": round(avg_pos, 2)}})
        updated += 1

    await db.gsc_snapshots.insert_one({
        "id": str(uuid.uuid4()), "siteId": site_id, "userId": user_id,
        "rowCount": len(rows), "articlesUpdated": updated,
        "syncedAt": utcnow_iso(),
    })
    await db.users.update_one(
        {"id": user_id},
        {"$set": {"lastGscSync": utcnow_iso()}})
    return {"synced": True, "lastSync": utcnow_iso(), "rowCount": len(rows),
            "articlesUpdated": updated}


# -------------------------------------------------------------- brand voice
async def run_brand_voice_training(job_id: str, site_id: str, user_id: str,
                                   samples: list[str]):
    db = get_db()
    await _update_job(job_id, status="running", progress=30)
    try:
        from services import brand_voice as _bv
        profile = await _bv.analyse_profile(samples)
        existing = await db.brand_voices.find_one(
            {"siteId": site_id}, {"_id": 0})
        doc = {
            "siteId": site_id, "userId": user_id,
            "styleProfile": profile,
            "formalityScore": int(profile.get("formality", 50)),
            "playfulnessScore": int(profile.get("playfulness", 50)),
            "technicalityScore": int(profile.get("technicality", 50)),
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
                          result={"brandVoiceId": result_id,
                                  "profile": profile})
    except Exception as e:
        logger.exception("brand voice training failed")
        await _update_job(job_id, status="failed", error=str(e))


# =========================================================================
# CRON entry points
# =========================================================================
async def cron_daily_article_generation():
    """Find eligible sites (autoPublish ON, active sub, CMS connected),
    create + run an article generation job for each. Skips sites that already
    have an article scheduled today.
    """
    db = get_db()
    logger.info("[CRON] daily article generation")
    today_iso = datetime.now(timezone.utc).date().isoformat()

    async for setting in db.article_settings.find({"autoPublish": True},
                                                   {"_id": 0}):
        site_id = setting["siteId"]
        user_id = setting["userId"]

        # 1. CMS connected?
        site = await db.sites.find_one(
            {"id": site_id, "deleted": {"$ne": True}}, {"_id": 0})
        if not site:
            continue
        connected = (site.get("wordpressConnected")
                     or site.get("webflowToken") or site.get("ghostApiKey")
                     or site.get("hubspotToken") or site.get("wixApiKey")
                     or site.get("notionToken"))
        if not connected:
            continue

        # 2. Active sub or trial
        sub = await db.subscriptions.find_one(
            {"userId": user_id, "status": {"$in": ["ACTIVE", "TRIALING"]}},
            {"_id": 0})
        if not sub:
            continue

        # 3. Already scheduled today?
        exists = await db.articles.find_one({
            "siteId": site_id,
            "createdAt": {"$gte": today_iso},
        }, {"_id": 0})
        if exists:
            continue

        # 4. Pick a topic — next PENDING search term, else AI suggest one
        pending = await db.search_terms.find_one(
            {"siteId": site_id, "status": "PENDING"}, {"_id": 0})
        if pending:
            topic = pending["term"]
            term_id = pending["id"]
        else:
            try:
                from services.llm import chat_completion
                topic = (await chat_completion(
                    "Reply with ONE short SEO topic title only, nothing else.",
                    f"Suggest a single SEO topic for the site {site.get('name')} "
                    f"({site.get('url')}).", model="gpt-4o")).strip().splitlines()[0]
            except Exception:
                topic = f"Insights for {site.get('name', 'your business')}"
            term_id = None

        article_id = str(uuid.uuid4())
        await db.articles.insert_one({
            "id": article_id, "siteId": site_id, "userId": user_id,
            "title": topic, "slug": "", "content": "",
            "searchTerm": topic, "status": "DRAFT", "wordCount": 0,
            "impressions": 0, "clicks": 0,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        })
        if term_id:
            await db.search_terms.update_one(
                {"id": term_id}, {"$set": {"status": "USED"}})
        job_id = await _create_job("article_generation",
                                   {"articleId": article_id})
        await run_article_generation(job_id, article_id, site_id, user_id, topic)


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


# ---------------------------------------------------- weekly digest email cron
async def cron_weekly_digest():
    """Monday 8am UTC — email each active user a weekly performance digest."""
    db = get_db()
    logger.info("[CRON] weekly digest")
    from services import email as _em
    async for user in db.users.find(
            {"deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "email": 1, "fullName": 1, "notifications": 1}):
        notif = user.get("notifications") or {}
        if not notif.get("weeklyScore", True):
            continue
        # Aggregate this week across all user sites
        sites = await db.sites.find(
            {"userId": user["id"], "deleted": {"$ne": True}},
            {"_id": 0, "id": 1}).to_list(50)
        site_ids = [s["id"] for s in sites]
        if not site_ids:
            continue
        latest_scores = await db.growth_scores.find(
            {"siteId": {"$in": site_ids}},
            {"_id": 0}).sort("calculatedAt", -1).limit(2).to_list(2)
        latest = latest_scores[0]["score"] if latest_scores else 0
        prev = latest_scores[1]["score"] if len(latest_scores) > 1 else latest
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        articles_week = await db.articles.count_documents({
            "userId": user["id"], "publishedAt": {"$gte": week_ago},
            "status": "PUBLISHED", "deleted": {"$ne": True},
        })
        top = await db.articles.find_one(
            {"userId": user["id"], "publishedAt": {"$gte": week_ago}},
            {"_id": 0, "title": 1, "clicks": 1},
            sort=[("clicks", -1)])
        try:
            await _em.weekly_digest(
                user_name=user.get("fullName", "there"),
                to=user["email"],
                growth_score=latest, score_change=latest - prev,
                articles_published=articles_week,
                top_article_title=(top or {}).get("title", "—"),
                top_article_clicks=(top or {}).get("clicks", 0),
                report_url=f"{os.environ.get('FRONTEND_URL', '')}/dashboard",
            )
        except Exception as e:
            logger.warning("weekly digest send failed for %s: %s",
                           user["email"], e)


# Expose helpers for routers
create_job = _create_job
update_job = _update_job
