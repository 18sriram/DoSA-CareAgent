"""Doctor availability and appointment slots, in the hospital's local time."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import config, db

SLOT_CHOICES = (10, 15, 20, 30, 45, 60)
MIN_NOTICE_MINUTES = 60          # nobody can book a slot that starts within the hour
HORIZON_DAYS = 28
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
now_fn = None                    # tests may replace this to freeze time


def local_now():
    """The hospital's current local time (naive). Works even where the system has no time-zone database."""
    if now_fn:
        return now_fn()
    try:
        now = datetime.now(ZoneInfo(config.TIMEZONE))
    except Exception:                                    # e.g. Windows without the 'tzdata' package
        fixed = {"Asia/Kolkata": timedelta(hours=5, minutes=30)}.get(config.TIMEZONE, timedelta(0))
        now = datetime.now(timezone.utc) + fixed
    return now.replace(tzinfo=None, second=0, microsecond=0)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60) or len(hhmm) != 5:
        raise ValueError(f"'{hhmm}' is not a time (use HH:MM)")
    return h * 60 + m


def validate_windows(windows) -> list:
    """Clean a weekly timetable. Raises ValueError with a plain message."""
    if not isinstance(windows, list) or len(windows) > 28:
        raise ValueError("The timetable is not valid.")
    clean = []
    for w in windows:
        try:
            wd, start, end = int(w["weekday"]), str(w["start"]), str(w["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Each session needs a day, a start and an end.")
        if not 0 <= wd <= 6:
            raise ValueError("Unknown day of the week.")
        if _minutes(start) >= _minutes(end):
            raise ValueError(f"{DAYS[wd]}: the end must be after the start.")
        clean.append({"weekday": wd, "start": start, "end": end})
    clean.sort(key=lambda w: (w["weekday"], w["start"]))
    for a, b in zip(clean, clean[1:]):
        if a["weekday"] == b["weekday"] and _minutes(b["start"]) < _minutes(a["end"]):
            raise ValueError(f"{DAYS[a['weekday']]}: two sessions overlap.")
    return clean


def free_slots(doctor_id: int, days: int = 14, limit: int = None) -> list:
    """Bookable start times (local 'YYYY-MM-DDTHH:MM'), soonest first, over `days` calendar days starting today."""
    sch = db.get_schedule(doctor_id)
    if not sch["windows"]:
        return []
    now = local_now()
    earliest = now + timedelta(minutes=MIN_NOTICE_MINUTES)
    off = {t["date"] for t in db.list_time_off(doctor_id)}
    first = now.date()
    booked = db.booked_starts(doctor_id, first.isoformat(), (first + timedelta(days=days)).isoformat())
    step, out = sch["slot_minutes"], []
    for offset in range(days):                       # `days` calendar days, starting today
        day = first + timedelta(days=offset)
        if day.isoformat() in off:
            continue
        for w in (w for w in sch["windows"] if w["weekday"] == day.weekday()):
            t, end = _minutes(w["start"]), _minutes(w["end"])
            while t + step <= end:
                slot = datetime(day.year, day.month, day.day, t // 60, t % 60)
                iso = slot.strftime("%Y-%m-%dT%H:%M")
                if slot >= earliest and iso not in booked:
                    out.append(iso)
                t += step
    out.sort()
    return out[:limit] if limit else out


def is_bookable(doctor_id: int, starts_at: str) -> bool:
    return starts_at in free_slots(doctor_id, days=HORIZON_DAYS)


def pick_to_offer(slots: list, n: int = 3) -> list:
    """Offer slots on different days where possible, so a caller is not read three times in a row from one morning."""
    chosen, days = [], set()
    for s in slots:
        if s[:10] not in days:
            chosen.append(s); days.add(s[:10])
        if len(chosen) == n:
            return chosen
    for s in slots:
        if s not in chosen:
            chosen.append(s)
        if len(chosen) == n:
            break
    return sorted(chosen)


# ---- how a slot is spoken (Telugu) and shown (English)
TE_DAYS = ["సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం", "ఆదివారం"]
TE_MONTHS = ["జనవరి", "ఫిబ్రవరి", "మార్చి", "ఏప్రిల్", "మే", "జూన్", "జూలై", "ఆగస్టు", "సెప్టెంబర్", "అక్టోబర్", "నవంబర్", "డిసెంబర్"]


def speak_slot(iso: str) -> str:
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M")
    period = "ఉదయం" if dt.hour < 12 else "మధ్యాహ్నం" if dt.hour < 16 else "సాయంత్రం" if dt.hour < 19 else "రాత్రి"
    h12 = dt.hour % 12 or 12
    return f"{TE_DAYS[dt.weekday()]}, {TE_MONTHS[dt.month - 1]} {dt.day}, {period} {h12}:{dt.minute:02d}"
