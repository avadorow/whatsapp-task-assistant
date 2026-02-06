import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple, Optional


def _parse_hhmm(s: Optional[str], default: str) -> time:
    s = (s or default).strip()
    hh, mm = s.split(":")
    return time(hour=int(hh), minute=int(mm))


def _dt_from_rfc3339(s: str) -> datetime:
    # Google returns RFC3339 like "2026-12-31T23:59:59Z" (Z = UTC)
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _subtract_busy(
    window: Tuple[datetime, datetime],
    busy: List[Tuple[datetime, datetime]],
) -> List[Tuple[datetime, datetime]]:
    ws, we = window
    out = [(ws, we)]
    for bs, be in busy:
        if be <= ws or bs >= we:
            continue
        new_out = []
        for cs, ce in out:
            if be <= cs or bs >= ce:
                new_out.append((cs, ce))
            else:
                if bs > cs:
                    new_out.append((cs, bs))
                if be < ce:
                    new_out.append((be, ce))
        out = new_out
    return out


def build_free_blocks(
    busy_blocks: List[Dict[str, Any]],
    now_utc: datetime,
    end_utc: datetime,
    *,
    min_block_min: int = 30,
    tz_name: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Returns free blocks (UTC ISO strings) that fall inside "allowed windows"
    and are at least min_block_min minutes long.
    """

    tz_name = (tz_name or os.getenv("TZ", "America/New_York") or "").strip() or "America/New_York"
    tz = ZoneInfo(tz_name)

    work_start = _parse_hhmm(os.getenv("WORKDAY_START"), "09:00")
    work_end   = _parse_hhmm(os.getenv("WORKDAY_END"),   "19:00")
    late_start = _parse_hhmm(os.getenv("LATE_START"),    "21:00")
    late_end   = _parse_hhmm(os.getenv("LATE_END"),      "02:00")

    now_local = now_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)

    d0 = now_local.date()
    d1 = end_local.date()
    days = (d1 - d0).days  # can be 0+; end_local should not be before now_local

    # Convert busy to local datetime tuples, clipped to [now_local, end_local]
    busy_local: List[Tuple[datetime, datetime]] = []
    for b in busy_blocks:
        bs = _dt_from_rfc3339(b["start"]).astimezone(tz)
        be = _dt_from_rfc3339(b["end"]).astimezone(tz)
        if be > now_local and bs < end_local:
            busy_local.append((max(bs, now_local), min(be, end_local)))
    busy_local = _merge_intervals(busy_local)

    # Allowed windows
    windows: List[Tuple[datetime, datetime]] = []
    for i in range(days + 1):
        day = d0 + timedelta(days=i)
        dow = day.weekday()  # Mon=0 ... Sun=6

        # Weekday work window
        if dow <= 4:
            ws = datetime.combine(day, work_start, tzinfo=tz)
            we = datetime.combine(day, work_end, tzinfo=tz)
            windows.append((ws, we))

        # Late window Mon-Thu nights only (your current rule)
        if dow in (0, 1, 2, 3):
            ls = datetime.combine(day, late_start, tzinfo=tz)
            le = datetime.combine(day + timedelta(days=1), late_end, tzinfo=tz)
            windows.append((ls, le))

    # Clip windows to [now_local, end_local], subtract busy, filter by length
    free: List[Tuple[datetime, datetime]] = []
    for ws, we in windows:
        ws = max(ws, now_local)
        we = min(we, end_local)
        if ws >= we:
            continue

        for ps, pe in _subtract_busy((ws, we), busy_local):
            if (pe - ps).total_seconds() >= min_block_min * 60:
                free.append((ps, pe))

    free = _merge_intervals(free)

    return [
        {"start": s.astimezone(timezone.utc).isoformat(), "end": e.astimezone(timezone.utc).isoformat()}
        for s, e in free
    ]


def build_free_any(
    busy_blocks: List[Dict[str, Any]],
    now_utc: datetime,
    end_utc: datetime,
) -> List[Dict[str, str]]:
    """
    Returns free blocks (UTC ISO strings) anywhere in [now_utc, end_utc],
    ignoring any "allowed window" rules.
    """

    busy_utc: List[Tuple[datetime, datetime]] = []
    for b in busy_blocks:
        bs = _dt_from_rfc3339(b["start"])
        be = _dt_from_rfc3339(b["end"])
        if be > now_utc and bs < end_utc:
            busy_utc.append((max(bs, now_utc), min(be, end_utc)))

    busy_utc = _merge_intervals(busy_utc)

    free: List[Tuple[datetime, datetime]] = []
    cursor = now_utc
    for s, e in busy_utc:
        if s > cursor:
            free.append((cursor, s))
        cursor = max(cursor, e)

    if cursor < end_utc:
        free.append((cursor, end_utc))

    return [
        {"start": s.astimezone(timezone.utc).isoformat(), "end": e.astimezone(timezone.utc).isoformat()}
        for s, e in free
    ]
