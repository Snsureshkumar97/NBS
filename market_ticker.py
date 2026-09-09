"""
market_ticker.py — the scrolling strip of world markets
================================================================================
The three indices this tool analyses are the only ones it has an opinion about,
and a ticker of three items is not a ticker. What runs across the top of the
screen is context: what Asia did overnight, where Europe and the US closed,
what crypto is doing. None of it feeds a signal. It is there because an Indian
index does not open in a vacuum, and because knowing the Dow fell 300 points
overnight changes how you read the first hour.

WHERE IT COMES FROM
    Yahoo's public chart endpoint — the same one FreeDataProvider already uses.
    No key, no account, no per-user token, so it works whether or not anybody
    has connected a broker.

WHY IT IS CACHED FOR A MINUTE
    These are not tradeable numbers here and nothing is computed from them.
    A closed market does not move at all, and an open one moving by the second
    would add nothing to a strip you read at a glance. One fetch a minute,
    shared by every user on the server, is the whole cost.

WHY A FAILURE IS SILENT
    If Yahoo is slow, rate-limits us, or changes its shape, the strip goes
    quiet and the rest of the screen carries on. Decoration must not be able
    to take down the thing it decorates.
"""

import threading
import time

import requests

# Ticker, label, and whether a number needs decimals to mean anything. Order is
# the order they scroll in: Asia, then Europe, then the US, then crypto —
# roughly the order the trading day actually happens in.
# Ticker, label, decimals, and which block it belongs to. India first and in
# depth, because that is what this tool is about and what the person watching
# actually trades — the sector indices say where the move is coming from, which
# a single NIFTY number cannot. The world block after it is context: an Indian
# index does not open in a vacuum.
#
# Every ticker here was checked against Yahoo rather than guessed. ^BSEMD (BSE
# Midcap) returns nothing and is deliberately absent.
MARKETS = [
    ("^NSEI",                "NIFTY 50",        2, "india"),
    ("^NSEBANK",             "BANK NIFTY",      2, "india"),
    ("^BSESN",               "SENSEX",          2, "india"),
    ("NIFTY_FIN_SERVICE.NS", "FIN NIFTY",       2, "india"),
    ("^INDIAVIX",            "INDIA VIX",       2, "india"),
    ("^NSEMDCP50",           "NIFTY MIDCAP 50", 2, "india"),
    ("^CRSLDX",              "NIFTY 500",       2, "india"),
    ("^CNXIT",               "NIFTY IT",        2, "india"),
    ("^CNXBANK" ,            "NIFTY BANK IDX",  2, "india"),
    ("^CNXAUTO",             "NIFTY AUTO",      2, "india"),
    ("^CNXPHARMA",           "NIFTY PHARMA",    2, "india"),
    ("^CNXFMCG",             "NIFTY FMCG",      2, "india"),
    ("^CNXMETAL",            "NIFTY METAL",     2, "india"),
    ("^CNXENERGY",           "NIFTY ENERGY",    2, "india"),
    ("^CNXPSUBANK",          "NIFTY PSU BANK",  2, "india"),
    ("^CNXINFRA",            "NIFTY INFRA",     2, "india"),
    ("^CNXREALTY",           "NIFTY REALTY",    2, "india"),
    ("^CNXMEDIA",            "NIFTY MEDIA",     2, "india"),
    ("INR=X",                "USD/INR",         2, "india"),

    ("^N225",                "NIKKEI",          0, "world"),
    ("^HSI",                 "HANG SENG",       0, "world"),
    ("^FTSE",                "FTSE 100",        2, "world"),
    ("^GDAXI",               "DAX",             2, "world"),
    ("^GSPC",                "S&P 500",         2, "world"),
    ("^DJI",                 "DOW JONES",       0, "world"),
    ("^IXIC",                "NASDAQ",          2, "world"),
    ("^VIX",                 "VIX",             2, "world"),
    ("GC=F",                 "GOLD",            1, "world"),
    ("BZ=F",                 "BRENT",           2, "world"),
    ("BTC-USD",              "BTC/USD",         0, "world"),
]

TTL = 90
_lock = threading.Lock()
_cache = {"at": 0.0, "rows": []}
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _one(ticker, label, dp):
    """One market's last price and change against the previous close.

    Two days of daily candles rather than a quote endpoint: the quote APIs
    move around and get blocked, while this is the same call the price
    provider already relies on.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    r = requests.get(url, params={"interval": "1d", "range": "5d"},
                     headers=_UA, timeout=6)
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result")
    if not result:
        return None
    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return None
    chg = pct = None
    if prev:
        chg = round(price - prev, 2)
        pct = round((price - prev) / prev * 100, 2)
    return {"label": label, "price": round(float(price), dp),
            "change": chg, "pct": pct, "dp": dp}


def rows(force=False):
    """The strip, cached. Never raises."""
    now = time.time()
    with _lock:
        if not force and _cache["rows"] and now - _cache["at"] < TTL:
            return list(_cache["rows"])
    out = []
    for ticker, label, dp, group in MARKETS:
        try:
            row = _one(ticker, label, dp)
        except Exception:
            row = None
        if row:
            row["group"] = group
            out.append(row)
        # A small gap between calls. Thirty requests in a burst looks like
        # scraping; spread over a couple of seconds it looks like a page load,
        # and nothing here is time-critical enough to care.
        time.sleep(0.05)
    with _lock:
        # A partial answer beats replacing a good strip with an empty one, so
        # a fetch that came back with nothing leaves the last good rows alone.
        if out:
            _cache["rows"] = out
            _cache["at"] = now
        return list(_cache["rows"])


def start_background(stop_event=None):
    """Keep the cache warm so no page load ever waits on Yahoo.

    Thirty sequential HTTP calls is a few seconds; doing that inside a request
    would make the first visitor of every cache window pay for it.
    """
    def loop():
        while stop_event is None or not stop_event.is_set():
            try:
                rows(force=True)
            except Exception:
                pass
            for _ in range(TTL):
                if stop_event is not None and stop_event.is_set():
                    return
                time.sleep(1)

    t = threading.Thread(target=loop, daemon=True, name="market-ticker")
    t.start()
    return t
