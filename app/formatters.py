from datetime import datetime
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from app.availibility import _dt_from_rfc3339

#Just for formatting calendar free blocks into local time
def _fmt_local(dt: datetime) -> str:
    # Format datetime in local TZ as ISO8601 string
    s = dt.strftime("%a %I:%M %p")
    return s.replace(" 0", " ")

def shape_blocks(blocks: List[Dict[str, str]], tz: ZoneInfo) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not blocks:
        return []
    for b in blocks:
        s_utc = _dt_from_rfc3339(b["start"])
        e_utc = _dt_from_rfc3339(b["end"])
        s_l = s_utc.astimezone(tz)
        e_l = e_utc.astimezone(tz)
        out.append({
            "start_utc": s_utc.isoformat(),
            "end_utc": e_utc.isoformat(),
            "start_local": _fmt_local(s_l),
            "end_local": _fmt_local(e_l),
            "minutes": int((e_utc - s_utc).total_seconds() // 60),
        })
    return out

def blocks_to_whatsapp(blocks: list, label: str) -> str:
    #formats blocks for whatsapp message
    if not blocks:
        return ""
    return "\n".join(
        f"{label}: {b['start_local']} – {b['end_local']} ({b['minutes']} min)"
        for b in blocks
    )
