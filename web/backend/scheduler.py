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


# A-share market window in minutes-from-midnight (server-local, matching
# ``compute_next_run_at``), with the same small buffer the quote merge uses.
# Lunch break is 11:30–13:00, during which we deliberately do NOT fire.
_MKT_OPEN = 9 * 60 + 25            # ~09:30 open
_MKT_MORNING_END = 11 * 60 + 32    # ~11:30 morning close
_MKT_AFTERNOON_OPEN = 13 * 60      # 13:00 afternoon open
_MKT_CLOSE = 15 * 60 + 5           # ~15:00 close


def _catch_up_fire_at(asset_type: Optional[str], ref: datetime) -> Optional[datetime]:
    """When an overdue daily/weekly schedule should first fire *today*, or None
    to fall back to the next normal occurrence (tomorrow / next week).

    Crypto runs immediately (24/7). A-shares (default): run now if in an active
    session; during the **lunch break we do NOT run** — defer to the afternoon
    open (13:00); before the morning open, fire at the open; after the close,
    return None so it waits for the next day. Public holidays aren't filtered
    (the safe direction — a holiday just means no same-day run).
    """
    at = (asset_type or "stock").lower()
    if at == "crypto":
        return ref.replace(microsecond=0)
    if ref.weekday() >= 5:
        return None
    hm = ref.hour * 60 + ref.minute
    if hm < _MKT_OPEN:                      # before open → fire at the open
        return ref.replace(hour=9, minute=30, second=0, microsecond=0)
    if hm <= _MKT_MORNING_END:              # morning session → now
        return ref.replace(microsecond=0)
    if hm < _MKT_AFTERNOON_OPEN:            # lunch break → defer to 13:00
        return ref.replace(hour=13, minute=0, second=0, microsecond=0)
    if hm <= _MKT_CLOSE:                    # afternoon session → now
        return ref.replace(microsecond=0)
    return None                             # after close → next day


def _in_trading_session(asset_type: Optional[str], ref: datetime) -> bool:
    """True if ``asset_type``'s market is in an active session right now —
    morning or afternoon, **lunch break excluded**. Crypto is always True."""
    at = (asset_type or "stock").lower()
    if at == "crypto":
        return True
    if ref.weekday() >= 5:
        return False
    hm = ref.hour * 60 + ref.minute
    return (_MKT_OPEN <= hm <= _MKT_MORNING_END) or (_MKT_AFTERNOON_OPEN <= hm <= _MKT_CLOSE)


def _next_session_start(asset_type: Optional[str], ref: datetime) -> datetime:
    """Next moment ``asset_type``'s market is open, at or after ``ref``.

    Returns ``ref`` (to the minute) if already in session; otherwise the next
    open: today's open / afternoon open / a following weekday's open. Used to
    start and resume ``interval`` schedules so intraday monitoring only runs
    during trading hours. Crypto is always open; public holidays aren't modelled.
    """
    at = (asset_type or "stock").lower()
    if at == "crypto":
        return ref.replace(second=0, microsecond=0)
    cur = ref
    for _ in range(8):  # at most a week ahead (skips weekends)
        if cur.weekday() < 5:
            hm = cur.hour * 60 + cur.minute
            if hm < _MKT_OPEN:
                return cur.replace(hour=9, minute=30, second=0, microsecond=0)
            if hm <= _MKT_MORNING_END:
                return cur.replace(second=0, microsecond=0)
            if hm < _MKT_AFTERNOON_OPEN:
                return cur.replace(hour=13, minute=0, second=0, microsecond=0)
            if hm <= _MKT_CLOSE:
                return cur.replace(second=0, microsecond=0)
        cur = (cur + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    return cur


def compute_first_run_at(
    schedule_type: str,
    interval_minutes: Optional[int],
    time_of_day: Optional[str],
    day_of_week: Optional[int],
    asset_type: Optional[str] = "stock",
    *,
    ref: Optional[datetime] = None,
) -> str:
    """First fire time at create-time, with a "smart catch-up" for daily/weekly.

    Normally a daily/weekly schedule created *after* its configured time waits
    until the next occurrence (tomorrow / next week). That surprises users who
    add an auto-trading portfolio mid-session expecting a same-day run. So if
    today's configured time has already passed (and, for weekly, today is the
    configured weekday), we bring the first run forward to *today* via
    ``_catch_up_fire_at`` — now if the market is in session, the afternoon open
    if it's the lunch break, and not at all after the close. Recurring runs
    after that still follow ``compute_next_run_at`` unchanged.
    """
    ref = ref or datetime.now()
    normal = compute_next_run_at(
        schedule_type, interval_minutes, time_of_day, day_of_week, ref=ref,
    )
    if schedule_type == "interval":
        # Start intraday monitoring at the next open trading moment (now if the
        # market is already in session). The _tick gate keeps later fires inside
        # trading hours, so we don't wait a whole interval before the first run.
        return _next_session_start(asset_type, ref).isoformat()
    if schedule_type not in ("daily", "weekly"):
        return normal
    if schedule_type == "weekly" and day_of_week is not None and ref.weekday() != day_of_week:
        return normal
    hh, mm = _parse_hhmm(time_of_day or "09:00")
    todays = ref.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if todays > ref:
        return normal  # configured time still ahead today → just wait for it
    fire = _catch_up_fire_at(asset_type, ref)
    if fire is None:
        return normal
    if fire < todays:  # never fire before the configured time
        fire = todays
    return fire.replace(microsecond=0).isoformat()


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
        # Strong refs to fire-and-forget run tasks. asyncio only holds a weak
        # ref to a bare create_task() result, so without this the GC can drop a
        # mid-flight run. Tasks remove themselves on completion.
        self._bg_tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> asyncio.Task:
        """create_task that retains a strong ref until the task finishes."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

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
        self._spawn(
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
            # Interval (intraday-monitoring) schedules only run during trading
            # hours. If one comes due off-session — overnight, lunch break,
            # weekend — don't fire; push next_run to the next session open so it
            # resumes cleanly instead of burning analyses while the market's shut.
            if s["schedule_type"] == "interval" and not _in_trading_session(s.get("asset_type"), now):
                resume = _next_session_start(s.get("asset_type"), now).isoformat()
                await loop.run_in_executor(
                    None,
                    lambda sid=sid, n=resume: db.update_schedule(sid, next_run_at=n),
                )
                continue
            self._in_flight[sid] = now
            self._spawn(self._fire_scheduled(s))

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
        from .routers.settings import get_effective_config

        loop = asyncio.get_running_loop()
        analysts = json.loads(schedule["analysts"])
        # Base on the current effective config (DEFAULT_CONFIG + Settings
        # overrides) so LLM provider/model/keys are populated, then overlay the
        # schedule's persisted config. Only non-None stored values win: the
        # screener / holdings "portfolio" paths persist llm_provider /
        # deep_think_llm / quick_think_llm = None, and feeding those straight to
        # GraphRunner gives it no LLM, which is why scheduled runs failed while
        # manual ones (which already merge effective config) succeeded.
        stored = json.loads(schedule["config_json"])
        config = dict(get_effective_config())
        for k, v in stored.items():
            if v is not None:
                config[k] = v
        ticker = schedule["ticker"]
        trade_date = datetime.now().strftime("%Y-%m-%d")
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
