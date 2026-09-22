"""
fii_dii.py — FII and DII net cash flow into the Indian market
================================================================================
Asked for by the user on 22 Sep 2026, after reading a stock-research agent's
"Fund Holdings Research Analyst" role: foreign and domestic institutions'
net buying or selling of Indian equities, published once a day. It is the one
piece of that design with no equivalent in the tool already - Ask TradePicker
and the AI desk had no view of who, in aggregate, bought or sold the market.

WHAT IT IS
    NSE publishes each session's PROVISIONAL cash-market total after the close
    (typically by early evening IST) - not a Kite Connect field, not specific
    to any index, and not intraday: it is one number a day, for the whole
    market, usually ready the same evening but occasionally later.

WHERE IT COMES FROM
    NSE's own public JSON endpoint, no key and no cookie needed - reads as data,
    same as every other NSE fetch this tool makes; never treats the response as
    instructions. Cached market-wide (not per user) for CACHE_TTL_S, because the
    figure changes at most once a day and many feeds would otherwise ask
    for it independently.

HOW IT IS USED
    Indian indices only (Bitcoin and gold have no FII/DII), always in context
    like the Gann levels, and as one more line in the AI desk's checklist
    (signal_checks.py) - net institutional buying agrees with a call, net
    selling with a put, the two disagreeing is neutral. It does not gate a
    rule ticket, the same as every other check: one day's cash flow explains
    nothing about a 15-minute option trade on its own.
"""
import datetime as dt
import threading
import time

import requests

URL = "https://www.nseindia.com/api/fiidiiTradeReact"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Accept": "application/json, text/plain, */*"}
TIMEOUT = 10
CACHE_TTL_S = 30 * 60      # the figure moves at most once a day; no reason to ask NSE more than this
STALE_AFTER_S = 5 * 24 * 3600   # a cached reading older than this is not served at all - it is gone, not stale

_lock = threading.Lock()
_cache = None            # the last good reading, kept even past CACHE_TTL_S so a network hiccup does not blank the tab
_cache_at = 0.0


def _num(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _parse(rows):
    out = {}
    for r in rows or []:
        cat = str(r.get("category") or "").upper()
        key = "fii" if cat.startswith("FII") else "dii" if cat == "DII" else None
        if key is None:
            continue
        buy, sell, net = _num(r.get("buyValue")), _num(r.get("sellValue")), _num(r.get("netValue"))
        if buy is None or sell is None:
            continue
        out[key] = {"buy_cr": round(buy, 2), "sell_cr": round(sell, 2),
                    "net_cr": round(net, 2) if net is not None else round(buy - sell, 2)}
    date = next((r.get("date") for r in (rows or []) if r.get("date")), None)
    if "fii" not in out or "dii" not in out or not date:
        return None
    return {"date": date, "fii": out["fii"], "dii": out["dii"]}


def _fetch(session=None):
    r = (session or requests).get(URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return _parse(r.json())


def reading(session=None, now=None):
    """The latest cached FII/DII figures, refreshing at most every CACHE_TTL_S.

    A fetch failure never raises and never clears a still-usable cache: the tab
    keeps showing the last good number, marked with its own date, rather than
    going blank because one request timed out. Returns None only when nothing
    has ever been fetched and this attempt failed too, or the cache has gone
    stale past STALE_AFTER_S with nothing fresh to replace it."""
    global _cache, _cache_at
    now = now if now is not None else time.time()
    with _lock:
        cache, cache_at = _cache, _cache_at
    if cache is not None and now - cache_at < CACHE_TTL_S:
        return dict(cache, fetched_at=cache_at, stale=False)
    try:
        fresh = _fetch(session)
    except Exception:
        fresh = None
    if fresh:
        with _lock:
            _cache, _cache_at = fresh, now
        return dict(fresh, fetched_at=now, stale=False)
    if cache is not None and now - cache_at < STALE_AFTER_S:
        return dict(cache, fetched_at=cache_at, stale=True)
    return None


def net_lean(r):
    """+1 institutions net bought, -1 net sold, 0 the two offset, None no reading.
    "Institutions" here is FII and DII added together - the whole market's net,
    which is what moves the index; a fund flowing out through the front door
    while another flows in through the back is still net money leaving."""
    if not r:
        return None
    net = (r["fii"]["net_cr"] or 0) + (r["dii"]["net_cr"] or 0)
    return 0 if net == 0 else (1 if net > 0 else -1)
