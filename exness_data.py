#!/usr/bin/env python3
"""
exness_data.py — Exness's own BTCUSD / XAUUSD price history, for research only
================================================================================
The user, 3 Oct 2026: "A, do the research backtest on exness data" - test the
tool on Exness prices (an ADX-gate sweep) WITHOUT connecting any account.

Exness publishes free tick history (no login, no key): one zip per symbol per
month at ticks.ex2archive.com, each a CSV of every quote change -
"Exness","Symbol","Timestamp" (UTC, ms),"Bid","Ask". This module turns each month
into 15-minute candles on the MID price and keeps, per candle, the spread actually
quoted (its average and its closing value) - on a CFD the spread IS the cost of
trading, so it is kept bar by bar rather than assumed.

    Volume   there is no traded volume on a CFD quote stream; the number of quote
             changes in the bar is kept instead (the usual activity proxy). The
             engine's VWAP weights by it - a tick-weighted VWAP.
    Times    candles are stamped at their START in IST, like every other history
             in this project; a bar is only emitted if it saw at least one quote.

Months are cached one file each (resumable - an interrupted download keeps every
month already done) and the downloaded zip is deleted once its month is built.
Nothing here is used by the live tool.

    python3 exness_data.py            # build / refresh both symbols
"""
import os
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

import backtest_intraday as bt

URL = "https://ticks.ex2archive.com/ticks/{s}/{y}/{m:02d}/Exness_{s}_{y}_{m:02d}.zip"
SYMBOLS = ("BTCUSD", "XAUUSD")
FIRST = (2023, 9)
IST = "Asia/Kolkata"
CHUNK = 2_000_000


def _dir():
    d = os.path.join(os.path.dirname(bt._cache_path("BTC", 3)), "exness")
    os.makedirs(d, exist_ok=True)
    return d


def months_until(last):
    y, m = FIRST
    out = []
    while (y, m) <= last:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def bars_from_ticks(ticks):
    """15-minute mid-price candles + spread from a tick frame with UTC `ts`,
    `bid`, `ask`. Returns the per-bar aggregates needed to MERGE chunks later
    (sum and count of spreads, not their mean), keyed by the bar's UTC start."""
    t = ticks.dropna(subset=["bid", "ask"])
    t = t[(t["bid"] > 0) & (t["ask"] >= t["bid"])]
    mid = (t["bid"] + t["ask"]) / 2
    spr = t["ask"] - t["bid"]
    key = t["ts"].dt.floor("15min")
    g = pd.DataFrame({"key": key.values, "mid": mid.values, "spr": spr.values,
                      "ts": t["ts"].values}).groupby("key", sort=True)
    return pd.DataFrame({"Open": g["mid"].first(), "High": g["mid"].max(), "Low": g["mid"].min(),
                         "Close": g["mid"].last(), "n": g["mid"].size(), "spr_sum": g["spr"].sum(),
                         "spr_close": g["spr"].last(), "first_ts": g["ts"].min(), "last_ts": g["ts"].max()})


def merge_parts(parts):
    """Combine chunk aggregates - a bar can straddle two chunks - into final bars."""
    df = pd.concat(parts)
    df = df.sort_values("first_ts")
    g = df.groupby(level=0, sort=True)
    out = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                        "Close": g["Close"].last(), "Volume": g["n"].sum().astype(float),
                        "spread_avg": g["spr_sum"].sum() / g["n"].sum(), "spread_close": g["spr_close"].last()})
    out.index = pd.DatetimeIndex(out.index).tz_localize("UTC").tz_convert(IST)
    out.index.name = "Date"
    return out


def build_month(args):
    sym, y, m = args
    path = os.path.join(_dir(), f"{sym}_{y}_{m:02d}_15m.csv")
    if os.path.exists(path):
        return sym, y, m, "cached"
    z = path.replace("_15m.csv", ".zip")
    req = urllib.request.Request(URL.format(s=sym, y=y, m=m), headers={"User-Agent": "nbs-exness-study"})
    with urllib.request.urlopen(req, timeout=300) as r, open(z, "wb") as fh:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            fh.write(b)
    parts = []
    for ch in pd.read_csv(z, usecols=["Timestamp", "Bid", "Ask"], chunksize=CHUNK,
                          dtype={"Bid": "float64", "Ask": "float64"}):
        ts = pd.to_datetime(ch["Timestamp"], format="%Y-%m-%d %H:%M:%S.%fZ", utc=True)
        parts.append(bars_from_ticks(pd.DataFrame({"ts": ts, "bid": ch["Bid"], "ask": ch["Ask"]})))
    bars = merge_parts(parts)
    tmp = path + ".tmp"
    bars.to_csv(tmp)
    os.replace(tmp, path)
    os.remove(z)
    return sym, y, m, f"{len(bars)} bars"


def load(sym):
    """Every cached month of `sym`, as one 15-minute frame (IST index)."""
    d = _dir()
    files = sorted(f for f in os.listdir(d) if f.startswith(sym + "_") and f.endswith("_15m.csv"))
    if not files:
        raise RuntimeError(f"no Exness history for {sym} - run python3 exness_data.py")
    frames = []
    for f in files:
        x = pd.read_csv(os.path.join(d, f), index_col=0)
        x.index = pd.to_datetime(x.index, utc=True).tz_convert(IST)
        frames.append(x)
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def build_all(last=(2026, 9), workers=4):
    jobs = [(s, y, m) for s in SYMBOLS for (y, m) in months_until(last)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for sym, y, m, what in ex.map(build_month, jobs):
            print(f"  {sym} {y}-{m:02d}: {what}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    build_all()
    for s in SYMBOLS:
        df = load(s)
        print(f"{s}: {len(df):,} bars {df.index.min()} -> {df.index.max()}  median spread "
              f"{df['spread_avg'].median():.3f}")
