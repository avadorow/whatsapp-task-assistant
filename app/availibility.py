import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Tuple
#This is a calendar scraper to determine when i'm free
def _parse_hhmm(s: str, default: str) -> time:
    # Parse "HH:MM" string into time object, with default
    s = (s or default).strip()
    hh, mm = s.split(":")
    return time(hour=int(hh), minute=int(mm))

def _dt_from_rfc3339(s: str) -> datetime:
    # Google returns RFC3399 ;ole "2026-12-31T23:59:59Z" (Z = UTC)
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)

def _merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    # Merge overlapping intervals with date time pairs
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0]) #sorting by start time
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged
    
def _subtract_busy(window: Tuple[datetime, datetime], busy:  List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    #subtract busy intervals
    ws,we = window
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
) -> List[Dict[str, str]]:
    """""
    Build free time blocks from busy calendar events.

    Args:
        busy_blocks: List of busy events with 'start' and 'end' in RFC3339.
        now_utc: Current UTC datetime.
        days: Number of days ahead to consider.
        busy_blocks: list of {"start": "...", "end": "..."} from Google freebusy
    Returns free blocks in local TZ as [{"start": iso, "end": iso}]
    """
    tz_name = (os.getenv("TZ", "America/New_York") or "").strip() or "America/New_York"
    tz = ZoneInfo(tz_name)

    # Use env-first, fallback-second defaults so missing env vars don't crash you.
    work_start = _parse_hhmm(os.getenv("WORKDAY_START"), "09:00")
    work_end   = _parse_hhmm(os.getenv("WORKDAY_END"),   "19:00")
    late_start = _parse_hhmm(os.getenv("LATE_START"),    "21:00")
    late_end   = _parse_hhmm(os.getenv("LATE_END"),      "02:00")
    min_block_min = int(os.getenv("MIN_BLOCK_MIN", "30"))

    now_local = now_utc.astimezone(tz)
    end_local = end_utc.astimezone(tz)

    # Derive how many whole days we need to generate windows for (inclusive).
    # This keeps your existing "days loop" structure without requiring a days param.
    d0 = now_local.date()
    d1 = end_local.date()
    days = max(0, (d1 - d0).days)

    #Conver busy to local dt tuples
    busy_local: List[Tuple[datetime, datetime]] = []
    for b in busy_blocks:
        bs = _dt_from_rfc3339(b["start"]).astimezone(tz)
        be = _dt_from_rfc3339(b["end"]).astimezone(tz)
        if be > now_local and bs < end_local:
            busy_local.append((max(bs, now_local), min(be, end_local)))
    busy_local = _merge_intervals(busy_local)

    #Build the allowed windows
    windows: List[Tuple[datetime, datetime]] = []
    for i in range(days + 1):
        day = d0 + timedelta(days=i)
        dow = day.weekday() # Mon = 0 sun = 6

        #Weekday time (mon-fri)
        if dow <= 4:
            ws = datetime.combine(day, work_start, tzinfo=tz)
            we = datetime.combine(day, work_end, tzinfo=tz)
            windows.append((ws, we))

        #Weeknight late window (mon-thu)
        if dow in [0, 1, 2, 3]:
            ls = datetime.combine(day, late_start, tzinfo=tz)
            le = datetime.combine(day + timedelta(days=1), late_end, tzinfo=tz)
            windows.append((ls, le))

    #Clip the windows to [now_local, end_local] and subtract busy
    free: List[Tuple[datetime, datetime]] = []
    for ws, we in windows:
        ws = max(ws, now_local)
        we = min(we, end_local)

        # If the clipped window is empty or negative, skip it
        if ws >= we:
            continue

        pieces = _subtract_busy((ws, we), busy_local)
        for ps, pe in pieces:
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
    # Convert busy blocks to UTC tuples and clip to [now_utc, end_utc]
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


    # Return ISO blocks in local TZ
    return [
    {"start": s.astimezone(timezone.utc).isoformat(), "end": e.astimezone(timezone.utc).isoformat()}
    for s, e in free
]


