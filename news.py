"""
news.py — headlines, fetched by the server and de-duplicated
================================================================================
Borrowed from OpenTerminal's news widget, with its two good decisions kept:
several sources rather than one, and the same story from three of them shown
once rather than three times.

WHAT THIS DOES AND DOES NOT SEND
    It reads public RSS feeds. Nothing about you goes out with the request -
    no account, no trades, no Zerodha session, not even which index you are
    looking at. The browser never talks to these sites either: the server
    fetches, and the page asks the server, so a headline publisher cannot see
    the tool's users at all.

WHY IT CANNOT BREAK THE SCREEN
    Every source is fetched in its own try/except with a short timeout, the
    answer is cached for five minutes, and a total failure returns an empty
    list with a note. News is decoration around a signal; it does not get to
    take the signal down with it.
"""
import email.utils
import re
import ssl
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

TIMEOUT = 8
TTL = 300                      # five minutes; these publish a few times an hour
PER_SOURCE = 12
UA = "Mozilla/5.0 (compatible; NBS Signal Tool; +https://nbstradingtool.vercel.app)"

SOURCES = {
    # Sources that serve an honestly-identified reader. Business Standard,
    # Moneycontrol and Zeebiz answer 403 to anything that does not claim to be
    # a browser; the tool says what it is and takes the no, rather than
    # dressing up as Chrome to get past them. NDTV Profit was dropped for a
    # different reason: its markets and business feeds are gone (404) and the
    # one that answers is general news - a trading screen does not need a
    # politics fact-check next to the signal.
    "nse_index": [
        ("Mint", "https://www.livemint.com/rss/markets"),
        ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
        ("BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
        ("BusinessLine", "https://www.thehindubusinessline.com/markets/stock-markets/feeder/default.rss"),
    ],
    "crypto": [
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ],
}

_lock = threading.Lock()
_cache = {}                    # market -> (fetched_at, items, note)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_KEY = re.compile(r"[^a-z0-9 ]+")


def _clean(text):
    return _WS.sub(" ", _TAG.sub(" ", text or "")).strip()


def _when(node):
    """Publication time as an epoch, from whichever field the feed uses."""
    for tag in ("pubDate", "published", "updated",
                "{http://www.w3.org/2005/Atom}published",
                "{http://www.w3.org/2005/Atom}updated"):
        el = node.find(tag)
        if el is None or not (el.text or "").strip():
            continue
        raw = el.text.strip()
        try:
            return email.utils.parsedate_to_datetime(raw).timestamp()
        except (TypeError, ValueError):
            pass
        try:
            import datetime as dt
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None


def _items(xml_text, source):
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    nodes = root.findall(".//item") or root.findall("{http://www.w3.org/2005/Atom}entry")
    for n in nodes[:PER_SOURCE]:
        title = n.find("title")
        if title is None:
            title = n.find("{http://www.w3.org/2005/Atom}title")
        link = n.find("link")
        if link is None:
            link = n.find("{http://www.w3.org/2005/Atom}link")
        href = ""
        if link is not None:
            href = (link.text or "").strip() or (link.get("href") or "").strip()
        text = _clean(title.text if title is not None else "")
        # Only ever http(s): a feed is data from outside, and an item is free
        # to put javascript: in its link.
        if not text or not href.lower().startswith(("http://", "https://")):
            continue
        out.append({"title": text[:180], "link": href, "source": source,
                    "ts": _when(n)})
    return out


_SSL = None


def _ctx():
    """An SSL context that can actually verify these certificates.

    This Python has no system CA bundle wired in, so a plain urlopen fails
    every HTTPS fetch with CERTIFICATE_VERIFY_FAILED - which is why the first
    run of this module returned "none of the sources answered" while curl
    fetched the same feeds fine. certifi ships the bundle; data_providers.py
    solves the identical problem the same way. Verification is never turned
    off: an unverified feed is worse than no feed.
    """
    global _SSL
    if _SSL is None:
        try:
            import certifi
            _SSL = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _SSL = ssl.create_default_context()
    return _SSL


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        return r.read(400_000).decode("utf-8", "replace")


def headlines(market="nse_index", limit=24, force=False):
    """(items, note) for one market, newest first, one story per headline."""
    now = time.time()
    with _lock:
        hit = _cache.get(market)
        if hit and not force and now - hit[0] < TTL:
            return hit[1][:limit], hit[2]

    items, failed = [], []
    for source, url in SOURCES.get(market, []):
        try:
            items += _items(_fetch(url), source)
        except Exception:
            failed.append(source)

    # The same story from three sites, once. Keyed on the words of the
    # headline, so punctuation and a trailing site name do not make it new.
    seen, unique = set(), []
    for it in sorted(items, key=lambda x: (x["ts"] is None, -(x["ts"] or 0))):
        key = _KEY.sub("", (it["title"] or "").lower())[:70]
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    note = ""
    if failed and unique:
        note = "Some sources did not answer: " + ", ".join(sorted(set(failed))) + "."
    elif failed and not unique:
        note = "No headlines right now - none of the sources answered."
    with _lock:
        _cache[market] = (now, unique, note)
    return unique[:limit], note
