"""APScheduler setup for cron-like background jobs."""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("jalwa.scheduler")
scheduler: AsyncIOScheduler | None = None


def start_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        return scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Lazy imports to avoid circular deps
    from services.jobs import (
        cron_daily_article_generation,
        cron_weekly_ai_visibility,
        cron_weekly_growth_score,
        cron_daily_gsc_sync,
        cron_hourly_social_publish,
        cron_weekly_digest,
    )
    from services.reminders import cron_reminders

    scheduler.add_job(cron_daily_article_generation,
                      CronTrigger(hour=6, minute=0))
    scheduler.add_job(cron_weekly_ai_visibility,
                      CronTrigger(day_of_week="mon", hour=8, minute=0))
    scheduler.add_job(cron_weekly_digest,
                      CronTrigger(day_of_week="mon", hour=8, minute=5))
    scheduler.add_job(cron_weekly_growth_score,
                      CronTrigger(day_of_week="mon", hour=7, minute=0))
    scheduler.add_job(cron_daily_gsc_sync, CronTrigger(hour=2, minute=0))
    scheduler.add_job(cron_hourly_social_publish, CronTrigger(minute=0))
    # Daily 9 AM UTC — trial-ending + renewal reminders
    scheduler.add_job(cron_reminders, CronTrigger(hour=9, minute=0))

    scheduler.start()
    logger.info("APScheduler started with 7 cron jobs")
    return scheduler


def stop_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
