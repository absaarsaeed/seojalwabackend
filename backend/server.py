"""SEO Jalwa main FastAPI app."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from core.database import close_db, get_db  # noqa: E402
from core.response import (APIError, api_error_handler,  # noqa: E402
                            generic_error_handler, http_error_handler, ok,
                            validation_error_handler)
from core.scheduler import start_scheduler, stop_scheduler  # noqa: E402
from services.api_keys import refresh_cache, schedule_cache_refresh  # noqa: E402

# Routers
from routers import (analytics, articles, article_settings, auth,  # noqa: E402
                      ai_visibility, ai_writer, auto_publish, billing,
                      dashboard, feedback, growth_score, notifications,
                      pages, plugin, public, search_terms, sites, social,
                      team, user, user_activity)
from routers.admin import (activity as admin_activity,  # noqa: E402
                            analytics as admin_analytics,
                            announcements as admin_announcements,
                            api_keys as admin_api_keys,
                            audit as admin_audit,
                            auth as admin_auth,
                            billing as admin_billing,
                            blog as admin_blog,
                            coupons as admin_coupons,
                            email_logs as admin_email_logs,
                            email_templates as admin_email_templates,
                            insights as admin_insights,
                            plans as admin_plans,
                            plugin as admin_plugin,
                            settings as admin_settings,
                            users as admin_users)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("jalwa.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await refresh_cache()
    except Exception as e:
        log.warning("api keys cache initial refresh failed: %s", e)
    schedule_cache_refresh()
    try:
        start_scheduler()
    except Exception as e:
        log.warning("scheduler not started: %s", e)

    # Seed plans + admin creds on first boot (idempotent)
    try:
        from seed import run_seed
        await run_seed()
        # Phase 3 Part 6 — seed default legal pages
        from routers.legal import seed_legal_pages
        await seed_legal_pages()
    except Exception as e:
        log.warning("seed skipped: %s", e)

    # Seed email templates (idempotent)
    try:
        from services.email_templates import seed_templates
        n = await seed_templates()
        if n:
            log.info("email_templates: seeded %d new templates", n)
    except Exception as e:
        log.warning("email_templates seed skipped: %s", e)

    yield
    stop_scheduler()
    await close_db()


app = FastAPI(
    title="SEO Jalwa API",
    description="All-in-one AI growth platform backend.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Error handlers
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(Exception, generic_error_handler)


# ── Phase 3 Part 5 — maintenance-mode middleware ──────────────────────
from fastapi.responses import JSONResponse  # noqa: E402

# Always-allowed prefixes (admin must be able to turn maintenance OFF)
_MAINT_ALLOWLIST_PREFIXES = (
    "/api/admin",
    "/api/auth/login",
    "/api/auth/admin",
    "/api/health",
    "/api/legal",
    "/health",
    "/api/docs",
    "/api/openapi.json",
    "/api/redoc",
)


@app.middleware("http")
async def maintenance_middleware(request, call_next):
    path = request.url.path
    if (not path.startswith("/api/")
            or any(path.startswith(p) for p in _MAINT_ALLOWLIST_PREFIXES)):
        return await call_next(request)
    try:
        db = get_db()
        # Two storage shapes — admin UI saves under the `general` doc;
        # legacy seeds write a dedicated key/value doc.
        general = await db.settings.find_one(
            {"id": "general"}, {"_id": 0,
                                "maintenanceMode": 1,
                                "maintenanceMessage": 1}) or {}
        legacy = await db.settings.find_one(
            {"key": "maintenance_mode"}, {"_id": 0}) or {}
        active = (bool(general.get("maintenanceMode"))
                  or bool(legacy.get("value")))
        if active:
            msg = (general.get("maintenanceMessage")
                   or legacy.get("maintenanceMessage")
                   or "We're under maintenance. Back shortly!")
            return JSONResponse(
                status_code=503,
                content={
                    "success": False, "error": msg,
                    "code": "MAINTENANCE_MODE",
                    "statusCode": 503,
                },
            )
    except Exception:
        pass
    return await call_next(request)


# Health checks — both /health (root) and /api/health
@app.get("/health")
async def root_health():
    try:
        await get_db().command("ping")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"
    from datetime import datetime, timezone
    return {"status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": db_status}


@app.get("/api/health")
async def api_health():
    return await root_health()


@app.get("/api/")
async def api_index():
    return ok({"name": "SEO Jalwa API", "version": "1.0.0",
               "docs": "/api/docs"})


# Mount all routers under /api
PREFIX = "/api"
app.include_router(auth.router, prefix=PREFIX)
app.include_router(sites.router, prefix=PREFIX)
app.include_router(social.router, prefix=PREFIX)
app.include_router(articles.router, prefix=PREFIX)
app.include_router(search_terms.router, prefix=PREFIX)
app.include_router(article_settings.router, prefix=PREFIX)
app.include_router(ai_visibility.router, prefix=PREFIX)
app.include_router(ai_writer.router, prefix=PREFIX)
app.include_router(auto_publish.router, prefix=PREFIX)
app.include_router(analytics.router, prefix=PREFIX)
app.include_router(growth_score.router, prefix=PREFIX)
app.include_router(team.router, prefix=PREFIX)
app.include_router(user.router, prefix=PREFIX)
app.include_router(billing.router, prefix=PREFIX)
app.include_router(plugin.router, prefix=PREFIX)
app.include_router(notifications.router, prefix=PREFIX)
app.include_router(user_activity.router, prefix=PREFIX)
app.include_router(dashboard.router, prefix=PREFIX)
app.include_router(pages.router, prefix=PREFIX)
app.include_router(feedback.public_router, prefix=PREFIX)
app.include_router(public.router, prefix=PREFIX)

# Phase 3 Part 6 — legal pages (public + admin)
from routers import legal as legal_module  # noqa: E402
app.include_router(legal_module.public_router, prefix=PREFIX)
app.include_router(legal_module.admin_router, prefix=PREFIX)

# Admin
app.include_router(admin_auth.router, prefix=PREFIX)
app.include_router(admin_users.router, prefix=PREFIX)
app.include_router(admin_plans.router, prefix=PREFIX)
app.include_router(admin_billing.router, prefix=PREFIX)
app.include_router(admin_coupons.router, prefix=PREFIX)
app.include_router(admin_blog.router, prefix=PREFIX)
app.include_router(admin_announcements.router, prefix=PREFIX)
app.include_router(admin_analytics.router, prefix=PREFIX)
app.include_router(admin_api_keys.router, prefix=PREFIX)
app.include_router(admin_settings.router, prefix=PREFIX)
app.include_router(admin_plugin.router, prefix=PREFIX)
app.include_router(admin_audit.router, prefix=PREFIX)
app.include_router(admin_activity.router, prefix=PREFIX)
app.include_router(admin_email_logs.router, prefix=PREFIX)
app.include_router(admin_email_templates.router, prefix=PREFIX)
app.include_router(admin_insights.router, prefix=PREFIX)
app.include_router(feedback.admin_router, prefix=PREFIX)
