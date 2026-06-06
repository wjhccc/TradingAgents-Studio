"""Background scheduler for running analyses on a recurring schedule.

A lightweight asyncio loop wakes up every ``_LOOP_INTERVAL_SEC`` seconds,
queries SQLite for rows whose ``next_run_at`` has passed, and fires an
analysis run for each one. Three consecutive failures auto-disable a
schedule (matches the "fail-safe" pattern other forks use; lets us avoid
spamming the LLM provider when a config is broken).

Times are stored in **server-local time** as naive ISO strings ("09:30"
means 09:30 server-local). This is the convention users expect when they
configure "every weekday at market open" — we don't make them think in UTC.

The scheduler reuses ``GraphRunner`` so that scheduled runs produce the
same DB rows (analyses, agent_events, agent_reports) as interactive runs,
which means History/Holdings/Decision-Log automatically reflect them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from . import database as db
from .graph_runner import GraphRunner

logger = logging.getLogger(__name__)

# How often the background loop wakes up to look for due schedules.
_LOOP_INTERVAL_SEC = 30

# Consecutive failure count that flips a schedule to 'disabled'.
_AUTO_DISABLE_AFTER = 3

# Don't catch up runs that were missed more than this long ago (e.g. after
# a multi-day server outage we shouldn't flood the LLM with backfill).
_MAX_CATCH_UP_SECONDS = 24 * 3600

# Once per day, after this server-local time, the scheduler marks the paper
# account to market and stores a NAV snapshot so the equity curve updates
# without anyone clicking the manual button. 15:05 ≈ just after A-share close.
_NAV_SNAPSHOT_AFTER_HHMM = (15, 5)

# Max number of scheduled analyses allowed to run concurrently. When more
# schedules come due at once (e.g. a screened portfolio all set to 09:30),
# the excess queue and start as slots free up. Keeps us from fanning out N
# parallel LLM + market-data calls and tripping provider rate limits / RSTs.
_MAX_CONCURRENT_RUNS = 3

# A single scheduled run shouldn't take longer than this. If one hangs past
# the timeout it's cancelled and recorded as a failure, so the watchdog frees
# its in-flight slot rather than letting it block that schedule forever.
_RUN_TIMEOUT_SEC = 20 * 60


def _now_iso() -> str:
    """Server-local ISO timestamp (no timezone marker)."""
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_iso(s: str) -> datetime:
    """Parse an ISO timestamp that may or may not have a trailing 'Z'."""
    return datetime.fromisoformat(s.rstrip("Z"))


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = (s or "").strip().split(":")
    if len(parts) != 2:
        return 9, 0
    try:
        return int(parts[0]) % 24, int(parts[1]) % 60
    except ValueError:
        return 9, 0


def compute_next_run_at(
    schedule_type: str,
    interval_minutes: Optional[int],
    time_of_day: Optional[str],
    day_of_week: Optional[int],
    *,
    ref: Optional[datetime] = None,
) -> str:
    """Compute the next fire time for a schedule, as a server-local ISO string.

    Used both at create-time (for the first fire) and after each fire (to
    advance ``next_run_at``).
    """
    ref = ref or datetime.now()
    if schedule_type == "interval":
        minutes = interval_minutes or 60
        return (ref + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
    if schedule_type == "daily":
        hh, mm = _parse_hhmm(time_of_day or "09:00")
        candidate = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= ref:
            candidate += timedelta(days=1)
        return candidate.isoformat()
    if schedule_type == "weekly":
        hh, mm = _parse_hhmm(time_of_day or "09:00")
        dow = day_of_week if day_of_week is not None else 0  # 0=Mon..6=Sun
        days_ahead = (dow - ref.weekday()) % 7
        candidate = (ref + timedelta(days=days_ahead)).replace(
            hour=hh, minute=mm, second=0, microsecond=0,
        )
        if candidate <= ref:
            candidate += timedelta(days=7)
        return candidate.isoformat()
    return (ref + timedelta(hours=1)).replace(microsecond=0).isoformat()


def _compute_next_for_row(schedule: dict, *, ref: Optional[datetime] = None) -> str:
    return compute_next_run_at(
        schedule["schedule_type"],
        schedule.get("interval_minutes"),
        schedule.get("time_of_day"),
        schedule.get("day_of_week"),
        ref=ref,
    )


class SchedulerService:
    """Background loop that fires due schedules.

    Lifecycle is bound to the FastAPI app via ``main.lifespan`` so the loop
    starts on server start and shuts down cleanly on Ctrl-C.
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        # Schedule IDs currently being processed → wall-clock start time, so we
        # don't double-fire if a run outlasts the next tick, and so the
        # watchdog can reclaim a slot whose run hung past _RUN_TIMEOUT_SEC.
        self._in_flight: dict[int, datetime] = {}
        # Caps how many runs execute at once; excess queue. Created in start()
        # because a Semaphore must bind to the running loop.
        self._run_sem: Optional[asyncio.Semaphore] = None
        # Last date (YYYY-MM-DD, server-local) we stored a NAV snapshot, so the
        # daily auto-snapshot fires at most once per calendar day.
        self._last_nav_date: Optional[str] = None

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._run_sem = asyncio.Semaphore(_MAX_CONCURRENT_RUNS)
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "Scheduler started, tick=%ds, max_concurrent=%d",
            _LOOP_INTERVAL_SEC, _MAX_CONCURRENT_RUNS,
        )

    async def stop(self):
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        logger.info("Scheduler stopped")

    async def trigger_manual(self, schedule_id: int) -> Optional[str]:
        """Fire a schedule once, without touching next_run_at or fail_count.

        Returns the new analysis_id, or ``None`` if the schedule isn't found.
        """
        loop = asyncio.get_running_loop()
        schedule = await loop.run_in_executor(None, db.get_schedule, schedule_id)
        if not schedule:
            return None
        analysis_id = str(uuid.uuid4())
        asyncio.create_task(
            self._run_analysis(schedule, analysis_id, advance_state=False),
        )
        return analysis_id

    async def _loop(self):
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=_LOOP_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        loop = asyncio.get_running_loop()
        now_iso = _now_iso()
        schedules = await loop.run_in_executor(None, db.due_schedules, now_iso)
        now = datetime.now()
        # Watchdog: reclaim slots held by runs that hung past the timeout. The
        # run task itself enforces the timeout (asyncio.wait_for) and clears its
        # own entry in finally; this is a backstop for a wedged event loop.
        for sid, started in list(self._in_flight.items()):
            if (now - started).total_seconds() > _RUN_TIMEOUT_SEC + 60:
                logger.warning("Schedule %s: in-flight slot stuck >%ds, reclaiming",
                               sid, _RUN_TIMEOUT_SEC)
                self._in_flight.pop(sid, None)
        for s in schedules:
            sid = s["id"]
            if sid in self._in_flight:
                continue
            # Skip ancient missed fires — bump next_run_at forward and move on.
            try:
                gap = (now - _parse_iso(s["next_run_at"])).total_seconds()
            except Exception:
                gap = 0
            if gap > _MAX_CATCH_UP_SECONDS:
                next_run = _compute_next_for_row(s, ref=now)
                await loop.run_in_executor(
                    None,
                    lambda sid=sid, n=next_run: db.update_schedule(sid, next_run_at=n),
                )
                logger.info(
                    "Schedule %s: skipped a missed fire (gap=%.0fs), next at %s",
                    sid, gap, next_run,
                )
                continue
            self._in_flight[sid] = now
            asyncio.create_task(self._fire_scheduled(s))

        # Daily NAV snapshot — once per day, after market close, so the paper
        # account's equity curve updates on its own.
        await self._maybe_snapshot_nav(now)

    async def _maybe_snapshot_nav(self, now: datetime):
        today = now.strftime("%Y-%m-%d")
        if self._last_nav_date == today:
            return
        if (now.hour, now.minute) < _NAV_SNAPSHOT_AFTER_HHMM:
            return
        loop = asyncio.get_running_loop()
        try:
            from .routers.paper import compute_and_store_nav_snapshot
            acct = await loop.run_in_executor(None, db.ensure_default_paper_account)
            snap = await loop.run_in_executor(
                None, compute_and_store_nav_snapshot, acct["id"],
            )
            self._last_nav_date = today
            logger.info("Daily NAV snapshot stored: %s total=%s", today, snap["total_value"])
        except Exception:
            logger.exception("Daily NAV snapshot failed")

    async def _fire_scheduled(self, schedule: dict):
        """Fire a schedule and advance its state (next_run_at, fail_count)."""
        sid = schedule["id"]
        try:
            # Advance next_run_at *before* the analysis (and before queueing on
            # the semaphore) so the loop won't re-pick this row while it waits
            # for a concurrency slot or runs.
            next_run = _compute_next_for_row(schedule)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, lambda: db.update_schedule(sid, next_run_at=next_run),
            )
            analysis_id = str(uuid.uuid4())
            # Throttle: only _MAX_CONCURRENT_RUNS analyses run at once; the rest
            # wait here. Timeout guards against a single run hanging forever.
            sem = self._run_sem
            try:
                if sem is not None:
                    await sem.acquire()
                success = await asyncio.wait_for(
                    self._run_analysis(schedule, analysis_id, advance_state=True),
                    timeout=_RUN_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning("Schedule %s: run timed out after %ds", sid, _RUN_TIMEOUT_SEC)
                success = False
            finally:
                if sem is not None:
                    sem.release()
            await loop.run_in_executor(
                None,
                lambda: db.record_schedule_fire(
                    sid,
                    success=success,
                    analysis_id=analysis_id if success else None,
                    next_run_at=next_run,
                    auto_disable_after=_AUTO_DISABLE_AFTER,
                ),
            )
        finally:
            self._in_flight.pop(sid, None)

    async def _run_analysis(
        self,
        schedule: dict,
        analysis_id: str,
        *,
        advance_state: bool,
    ) -> bool:
        """Create the analysis row and run the graph. Returns True on success.

        ``advance_state`` is informational only — both manual and scheduled
        runs go through the same GraphRunner here, but only scheduled runs
        invoke record_schedule_fire afterwards.
        """
        loop = asyncio.get_running_loop()
        analysts = json.loads(schedule["analysts"])
        config = json.loads(schedule["config_json"])
        ticker = schedule["ticker"]
        trade_date = datetime.now().strftime("%Y-%m-%d")
        config = dict(config)
        config["_ticker"] = ticker
        config["_trade_date"] = trade_date
        try:
            await loop.run_in_executor(
                None,
                lambda: db.create_analysis(
                    id=analysis_id,
                    ticker=ticker,
                    trade_date=trade_date,
                    asset_type=schedule.get("asset_type", "stock"),
                    analysts=analysts,
                    config=config,
                ),
            )
            queue: asyncio.Queue = asyncio.Queue()  # nobody listens for scheduled runs
            runner = GraphRunner(analysis_id, config, analysts, queue)
            result = await runner.run()
            ok = result is not None
            # Auto-trade hook: if this schedule has auto_trade enabled and the
            # analysis completed, turn its decision into a paper order. Failures
            # here are logged but never fail the schedule fire.
            if ok and schedule.get("auto_trade"):
                await self._auto_trade(schedule, analysis_id)
            return ok
        except Exception:
            logger.exception("Schedule %s analysis %s failed", schedule["id"], analysis_id)
            return False

    async def _auto_trade(self, schedule: dict, analysis_id: str):
        loop = asyncio.get_running_loop()
        try:
            from .routers.paper import execute_auto_trade
            frac = schedule.get("auto_trade_cash_fraction") or 0.1
            order, reason = await loop.run_in_executor(
                None,
                lambda: execute_auto_trade(analysis_id, cash_fraction=float(frac)),
            )
            logger.info(
                "Schedule %s auto-trade (%s): %s",
                schedule["id"], "filled" if order else "skipped", reason,
            )
        except Exception:
            logger.exception("Schedule %s auto-trade crashed", schedule["id"])


# Module-level singleton — main.lifespan starts/stops this.
service = SchedulerService()
