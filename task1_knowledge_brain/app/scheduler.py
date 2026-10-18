"""Daily incremental sync of the help center, running inside the API process."""

from __future__ import annotations

import logging
import os
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)


def start_scheduler(get_service: Callable) -> AsyncIOScheduler:
    hour = int(os.getenv("KB_SYNC_HOUR_UTC", "3"))

    async def daily_sync() -> None:
        service = get_service()
        if service.ingestion_running:
            log.info("Daily sync skipped: a run is already in progress.")
            return
        report = await service.run_ingestion(mode="incremental")
        log.info("Daily sync finished: %s", report)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(daily_sync, "cron", hour=hour, minute=0, id="daily_sync", coalesce=True,
                      max_instances=1, misfire_grace_time=3600)
    scheduler.start()
    log.info("Daily help-center sync scheduled at %02d:00 UTC", hour)
    return scheduler
