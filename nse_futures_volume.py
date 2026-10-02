#!/usr/bin/env python3
"""
nse_futures_volume.py — real NIFTY/Bank Nifty futures volume, from NSE's own archives
================================================================================
Built 2 Oct 2026 for the volume-vote study: the index itself (NIFTY, Bank
Nifty) has no volume - it is a value, not a traded instrument, and the
cached 15-minute history confirms this directly (Volume is 0 for every row).
The volume that means anything is the near-month FUTURE's. Zerodha's own
historical API cannot reach it: `instruments()` only ever lists the current
~3 months of contracts, with no path to an expired one's token - confirmed
empirically, not assumed. NSE's own public bhavcopy archives go back the
full 3 years with no login needed, so this fetches from there instead.

SENSEX is deliberately NOT here. It trades on BSE, which (after real
searching - direct URLs, known downloader tools' source, BSE's own bhavcopy
page) has no comparably straightforward public F&O archive. Per the user,
2 Oct 2026: proceed with NIFTY/Bank Nifty now, SENSEX abstains (no data)
rather than block on it.

TWO ARCHIVE FORMATS, CONFIRMED EMPIRICALLY (both still live, overlapping)
    archives.nseindia.com/.../DERIVATIVES/{YYYY}/{MON}/fo{DDMMMYYYY}bhav.csv.zip
        Confirmed working 18 Sep 2023 (the start of this project's cached
        index history) through at least 5 Jul 2024.
    nsearchives.nseindia.com/.../BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip
        Confirmed working from at least 1 Jan 2024 through today - the two
        archives overlap for months, there is no gap to stitch across.
    CUTOVER below (8 Jul 2024) is an arbitrary split inside that overlap,
    not a real boundary - picked only so every date maps to exactly one URL.

RATE LIMITING: NSE's archive 404s transiently under rapid requests - not a
missing file, confirmed by retrying the SAME "404" alone after a pause and
getting 200. REQ_SLEEP below is deliberately generous for this reason.
"""
import csv
import datetime as dt
import io
import os
import time
import zipfile

import pandas as pd
import requests

CACHE_DIR = os.path.expanduser("~/trading-tool-logs/history")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
CUTOVER = dt.date(2024, 7, 8)
REQ_SLEEP = 3.0
SYMBOLS = ("NIFTY", "BANKNIFTY")


def _old_url(d):
    return (f"https://archives.nseindia.com/content/historical/DERIVATIVES/"
            f"{d.year}/{d.strftime('%b').upper()}/fo{d.strftime('%d%b%Y').upper()}bhav.csv.zip")


def _new_url(d):
    return (f"https://nsearchives.nseindia.com/content/fo/"
            f"BhavCopy_NSE_FO_0_0_0_{d.strftime('%Y%m%d')}_F_0000.csv.zip")


def _parse(raw_text):
    """Every FUTIDX/IDF row for NIFTY and BANKNIFTY in one day's bhavcopy,
    whichever of the two column layouts this file turns out to use -
    detected from the header itself, not from which URL it came from (the
    two archives overlap, so date alone does not say which format a
    specific file is in)."""
    reader = csv.DictReader(io.StringIO(raw_text))
    fields = reader.fieldnames or []
    old = "INSTRUMENT" in fields
    out = []
    for row in reader:
        if old:
            inst, sym, expiry_s = row.get("INSTRUMENT"), row.get("SYMBOL"), row.get("EXPIRY_DT")
            vol, oi = row.get("CONTRACTS"), row.get("OPEN_INT")
            op, cl = row.get("OPEN"), row.get("CLOSE")
            is_fut = inst == "FUTIDX"
        else:
            inst, sym, expiry_s = row.get("FinInstrmTp"), row.get("TckrSymb"), row.get("XpryDt")
            vol, oi = row.get("TtlTradgVol"), row.get("OpnIntrst")
            op, cl = row.get("OpnPric"), row.get("ClsPric")
            is_fut = inst == "IDF"
        if not is_fut or sym not in SYMBOLS:
            continue
        try:
            expiry = pd.to_datetime(expiry_s).date()
            out.append({"symbol": sym, "expiry": expiry.isoformat(),
                        "volume": float(vol), "oi": float(oi),
                        "open": float(op), "close": float(cl)})
        except (TypeError, ValueError):
            continue
    return out


def _fetch_day(d, session):
    """This day's rows, or None if nothing usable came back (holiday,
    transient block, format surprise) - never raises, a fetch loop over
    hundreds of days must not die on one bad one."""
    url = _old_url(d) if d < CUTOVER else _new_url(d)
    try:
        resp = session.get(url, headers={"User-Agent": UA}, timeout=20)
    except Exception:
        return None
    if resp.status_code != 200 or len(resp.content) < 200:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            raw = zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    except Exception:
        return None
    return _parse(raw)


def fetch(trading_days, use_cache=True, progress_every=25):
    """trading_days: an iterable of date objects to fetch, in order - the
    caller supplies these (from the SAME index history already cached, so
    this lines up exactly with what the rest of the backtest uses, instead
    of this file guessing its own trading calendar).

    Returns a DataFrame: date, symbol, expiry, volume, oi, open, close - one
    row per (day, symbol, contract) actually traded that day (several
    contracts can trade the same day; the caller picks the front month).
    open/close are the FRONT-MONTH FUTURE's own day candle, not the index's
    - needed because "rising volume" has no sign of its own; it only means
    something read against which way price actually moved that day (the
    standard OBV-style reading - volume_vote_study.py). Cached to disk,
    appended to incrementally as each day is fetched, so an interrupted run
    loses only the day in flight, never the whole fetch.
    """
    path = os.path.join(CACHE_DIR, "nse_futures_volume.csv")
    os.makedirs(CACHE_DIR, exist_ok=True)
    have = set()
    if use_cache and os.path.exists(path):
        cached = pd.read_csv(path, dtype={"date": str})
        have = set(cached["date"])
    else:
        cached = pd.DataFrame({"date": pd.Series(dtype=str), "symbol": pd.Series(dtype=str),
                                "expiry": pd.Series(dtype=str), "volume": pd.Series(dtype=float),
                                "oi": pd.Series(dtype=float), "open": pd.Series(dtype=float),
                                "close": pd.Series(dtype=float)})

    todo = [d for d in trading_days if d.isoformat() not in have]
    if not todo:
        print(f"  nse_futures_volume: {len(have)} days already cached, nothing to fetch")
        return cached

    print(f"  nse_futures_volume: fetching {len(todo)} new day(s) "
          f"({len(have)} already cached)")
    session = requests.Session()
    new_rows, fails = [], []
    for n, d in enumerate(todo, 1):
        rows = _fetch_day(d, session)
        if rows:
            for r in rows:
                new_rows.append({"date": d.isoformat(), **r})
        else:
            fails.append(d.isoformat())
        if n % progress_every == 0 or n == len(todo):
            print(f"  nse_futures_volume: {n}/{len(todo)} fetched, "
                  f"{len(fails)} failed/empty so far")
            # Flushed periodically, not just at the end - a run this long
            # (hundreds of requests, each rate-limited) is exactly the kind
            # that gets interrupted, and partial progress must survive that.
            if new_rows:
                out = pd.concat([cached, pd.DataFrame(new_rows)], ignore_index=True)
                out.to_csv(path, index=False)
        time.sleep(REQ_SLEEP)

    if fails:
        print(f"  nse_futures_volume: {len(fails)} day(s) had nothing usable "
              f"(holidays expected; anything else is worth a look): "
              + ", ".join(fails[:10]) + (" ..." if len(fails) > 10 else ""))
    final = pd.concat([cached, pd.DataFrame(new_rows)], ignore_index=True) if new_rows else cached
    final = final.drop_duplicates(subset=["date", "symbol", "expiry"]).sort_values("date")
    final.to_csv(path, index=False)
    return final


def front_month_series(raw, symbol):
    """raw: fetch()'s own DataFrame. For one symbol, the FRONT-MONTH
    contract's volume and OI on each day it traded - the nearest expiry
    that had not yet passed as of that day, decided from the data itself
    (which contracts actually traded that day), not from a computed
    expiry-day rule. Returns a DataFrame indexed by date."""
    df = raw[raw["symbol"] == symbol].copy()
    if df.empty:
        return pd.DataFrame(columns=["volume", "oi"])
    df["date"] = pd.to_datetime(df["date"])
    df["expiry"] = pd.to_datetime(df["expiry"])
    df = df[df["expiry"] >= df["date"]]
    front = (df.sort_values(["date", "expiry"])
               .groupby("date", as_index=True)
               .first()[["volume", "oi", "open", "close"]])
    return front


if __name__ == "__main__":
    import backtest_intraday as bt
    hist = bt.fetch_history("NIFTY", years=3, use_cache=True)
    days = sorted(set(hist.index.date))
    print(f"Fetching NSE futures volume for {len(days)} trading days "
          f"({days[0]} to {days[-1]})...")
    raw = fetch(days)
    for sym in SYMBOLS:
        fm = front_month_series(raw, sym)
        print(f"{sym}: {len(fm)} days with a front-month reading, "
              f"{len(days) - len(fm)} without")
