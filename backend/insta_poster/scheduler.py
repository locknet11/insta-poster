from __future__ import annotations

import asyncio
import logging

from insta_poster.service import InstaPosterService

logger = logging.getLogger("insta_poster.scheduler")


class BackgroundScheduler:
    """Lightweight asyncio loop that publishes due scheduled posts & stories."""

    def __init__(self, service: InstaPosterService, interval_seconds: float = 15.0) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="insta-poster-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                results = await asyncio.to_thread(self.service.run_due_scheduled)
                for pub in results:
                    logger.info(
                        "Scheduled %s %s finished with status=%s",
                        pub.pub_type,
                        pub.id,
                        pub.status,
                    )
            except Exception:
                logger.exception("Error while checking due scheduled publications")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue
