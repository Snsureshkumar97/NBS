"""
market_calendar.py — NSE's own trading calendar and live market status, read from nseindia.com
================================================================================
The user, 20 Oct 2026, on Diwali's Muhurat session (a Sunday): "not only for one anything in future it happens it have
show whatever zerodha shows". Zerodha takes its holidays and special sessions from the exchange and has no API for
them; NSE publishes both:
  HOLIDAYS  /api/holiday-master?type=trading - every trading holiday by NSE's own name, per segment, the year NSE has
            published. A name ending in "*" is a SPECIAL SESSION day (Diwali Laxmi Pujan* - Muhurat trading), whose
            hours come by circular. Read every 6 hours and kept on disk, so a restart without the network still knows.
  STATUS    /api/marketStatus - the exchange's live "Market is Open" / "Market is Closed", read every minute.
Both are fetched only by the background thread start_background() runs (the web server starts it): a request thread
only ever reads what is already here, so NSE being slow or down can never hold a page up (the 16 Oct strike-range
pile-up). Nothing is fetched in a process that never started it - tests, studies, the desktop app - and there the
tool keeps using main.NSE_HOLIDAYS_BY_YEAR as before.

The F&O segment's list is used: the tool trades index options. A year is taken from NSE only when NSE lists at least
MIN_PER_YEAR holidays in it (a garbled reply must not empty a year); otherwise main's own list stands for that year.
"""
import datetime as dt
import json
import os
import threading
import time

import config

HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
STATUS_URL = "https://www.nseindia.com/api/marketStatus"
SEGMENT = "FO"
HOLIDAY_EVERY_S = 6 * 3600
STATUS_EVERY_S = 60
STATUS_FRESH_S = 10 * 60          # an older status is not shown: it may no longer be true
MIN_PER_YEAR = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36",
           "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}

_lock = threading.Lock()
_holidays = {}                     # date -> NSE's name (the "*" taken off)
_specials = set()                  # dates NSE marks with "*": a special session
_status = None                     # {"open": bool, "text": str, "trade_date": date | None, "at": epoch seconds}
_noted = {}                        # what was last logged per feed, so a failure is said once, not every minute


# ---------------------------------------------------------------- reading what is here
def holiday_name(d):
    """NSE's own name for `d` as an F&O trading holiday, or None (not one, or NSE's list not loaded for its year)."""
    with _lock:
        return _holidays.get(d)


def years():
    """The years NSE's own list is loaded for."""
    with _lock:
        return {d.year for d in _holidays}


def special_session(d, now=None):
    """A special trading session on `d`: NSE's name for the day ("Diwali Laxmi Pujan"), "" for one NSE has not named,
    or None for no special session.

    Either NSE's calendar marks the day with "*" (Diwali's Muhurat trading), or the exchange's live status says it is
    open on a day the calendar has shut - a weekend or a holiday (a Saturday budget-day session)."""
    with _lock:
        if d in _specials:
            return _holidays.get(d) or ""
    st = status(now)
    if st and st["open"] and st.get("trade_date") == d:
        name = holiday_name(d)
        if name or d.weekday() >= 5:
            return name or ""
    return None


def status(now=None):
    """The exchange's live status - {"open", "text", "trade_date", "at"} - or None when none is fresh."""
    with _lock:
        st = dict(_status) if _status else None
    t = time.time() if now is None else (now.timestamp() if isinstance(now, dt.datetime) else now)
    if not st or abs(t - st["at"]) > STATUS_FRESH_S:
        return None
    return st


# ---------------------------------------------------------------- taking NSE's replies in
def _date(s):
    for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def ingest_holidays(data):
    """Take NSE's holiday-master reply in. Returns how many holidays were taken. A year with fewer than MIN_PER_YEAR
    is left to main's own list; an unusable reply changes nothing."""
    rows = (data or {}).get(SEGMENT) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return 0
    got, specials = {}, set()
    for r in rows:
        d = _date((r or {}).get("tradingDate"))
        name = str((r or {}).get("description") or "").strip()
        if not d or not name:
            continue
        if name.endswith("*"):
            specials.add(d)
            name = name.rstrip("*").strip()
        got[d] = name
    per_year = {}
    for d in got:
        per_year[d.year] = per_year.get(d.year, 0) + 1
    keep = {y for y, n in per_year.items() if n >= MIN_PER_YEAR}
    if not keep:
        return 0
    with _lock:
        for d in [d for d in _holidays if d.year in keep]:
            _holidays.pop(d)
            _specials.discard(d)
        for d, name in got.items():
            if d.year in keep:
                _holidays[d] = name
                if d in specials:
                    _specials.add(d)
    return sum(per_year[y] for y in keep)


def ingest_status(data, at=None):
    """Take NSE's marketStatus reply in (the Capital Market row - what the index and its options follow)."""
    rows = (data or {}).get("marketState") if isinstance(data, dict) else None
    row = next((r for r in rows or [] if str((r or {}).get("market", "")).lower() == "capital market"), None)
    if not row:
        return False
    raw = str(row.get("marketStatus") or "").strip()
    text = str(row.get("marketStatusMessage") or raw).strip()
    global _status
    with _lock:
        _status = {"open": raw.lower() == "open", "text": text, "trade_date": _date(row.get("tradeDate")),
                   "at": time.time() if at is None else at}
    return True


# ---------------------------------------------------------------- the background refresh
def _cache_path():
    return os.path.join(config.home_config_dir(), "nse_calendar.json")


def _save(data):
    try:
        path = _cache_path()
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"saved": time.time(), "holidays": data}, fh)
        os.replace(tmp, path)
    except Exception:
        pass


def _load_saved():
    try:
        with open(_cache_path()) as fh:
            ingest_holidays(json.load(fh).get("holidays"))
    except Exception:
        pass


def _note(feed, ok, detail=""):
    """One log line when a feed starts failing, and one when it recovers - not one a minute."""
    if _noted.get(feed) == ok:
        return
    _noted[feed] = ok
    print(f"market_calendar: NSE {feed} {'back' if ok else 'unavailable'}{(' - ' + detail) if detail else ''}", flush=True)


def _get(url):
    import requests
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


def refresh_holidays():
    try:
        data = _get(HOLIDAY_URL)
        n = ingest_holidays(data)
        if n:
            _save(data)
        _note("holidays", bool(n), "" if n else "a reply with no usable year")
    except Exception as exc:
        _note("holidays", False, type(exc).__name__)


def refresh_status():
    try:
        _note("status", ingest_status(_get(STATUS_URL)))
    except Exception as exc:
        _note("status", False, type(exc).__name__)


def start_background(stop_event=None):
    """Load the saved holiday list, then keep both fresh. The web server calls this once."""
    _load_saved()

    def loop():
        last_h = 0.0
        while stop_event is None or not stop_event.is_set():
            began = time.time()
            if began - last_h >= HOLIDAY_EVERY_S:
                refresh_holidays()
                last_h = began
            refresh_status()
            while time.time() - began < STATUS_EVERY_S:
                if stop_event is not None and stop_event.is_set():
                    return
                time.sleep(1)

    t = threading.Thread(target=loop, daemon=True, name="nse-calendar")
    t.start()
    return t
