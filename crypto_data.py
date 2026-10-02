#!/usr/bin/env python3
"""
crypto_data.py — the extra BTC history a real options backtest needs
================================================================================
backtest_intraday.fetch_history("BTC") has 3 years of 15-minute perpetual
candles (Deribit) and nothing else. Every earlier BTC study therefore scored
trades in raw price points - which describes a perpetual position, but the
live tool BUYS OPTIONS on Delta Exchange India (delta_orders.py), where time
decay, the volatility premium paid, spreads and fees decide the result.
This module adds, each from a free public endpoint and cached to disk next to
the candles:

    dvol()      Deribit's BTC volatility index (DVOL), hourly - the market's
                30-day implied volatility, used as the option-pricing vol.
    funding()   BTC-PERPETUAL funding, hourly (Deribit's interest_8h) - one of
                the pasted checklist's "derivative market" votes.
    candles()   the cached 3-year 15-minute history, extended to the present
                with the same Deribit perpetual series (so the study's last
                weeks overlap the user's real live record).

Nothing here is used by the live tool; it is research data only.
"""
import json
import os
import time
import urllib.parse
import urllib.request

import pandas as pd

import backtest_intraday as bt
import config

DERIBIT = "https://www.deribit.com/api/v2/public/"
IST = "Asia/Kolkata"


def _history_dir():
    return os.path.dirname(bt._cache_path("BTC", 3))


def _get(method, **params):
    url = DERIBIT + method + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "nbs-crypto-study"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)["result"]
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def _ms(ts):
    return int(pd.Timestamp(ts).timestamp() * 1000)


def _cached(name, build, refresh):
    path = os.path.join(_history_dir(), name)
    if os.path.exists(path) and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index, utc=True).tz_convert(IST)
        return df
    df = build()
    df.to_csv(path)
    return df


def dvol(start="2023-09-01", end=None, refresh=False):
    """Hourly DVOL close (annualised implied vol, in percent), IST index."""
    def build():
        stop = pd.Timestamp(end) if end else pd.Timestamp.utcnow().tz_localize(None)
        t = pd.Timestamp(start)
        rows = []
        while t < stop:
            nxt = min(t + pd.Timedelta(hours=900), stop)
            res = _get("get_volatility_index_data", currency="BTC",
                       start_timestamp=_ms(t), end_timestamp=_ms(nxt), resolution=3600)
            rows.extend(res.get("data") or [])
            t = nxt
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).drop_duplicates("ts")
        df.index = pd.DatetimeIndex(pd.to_datetime(df["ts"], unit="ms", utc=True)).tz_convert(IST)
        return df[["close"]].rename(columns={"close": "dvol"}).sort_index()
    return _cached("BTC_dvol_1h.csv", build, refresh)


def funding(start="2023-09-01", end=None, refresh=False):
    """Hourly perpetual funding, as Deribit's interest_8h (a fraction, e.g.
    0.0001 = 0.01% per 8 hours), IST index."""
    def build():
        stop = pd.Timestamp.utcnow().tz_localize(None)
        t = pd.Timestamp(start)
        rows = []
        while t < stop:
            nxt = min(t + pd.Timedelta(days=30), stop)
            res = _get("get_funding_rate_history", instrument_name="BTC-PERPETUAL",
                       start_timestamp=_ms(t), end_timestamp=_ms(nxt))
            rows.extend(res or [])
            t = nxt
        df = pd.DataFrame(rows).drop_duplicates("timestamp")
        df.index = pd.DatetimeIndex(pd.to_datetime(df["timestamp"], unit="ms", utc=True)).tz_convert(IST)
        return df[["interest_8h"]].rename(columns={"interest_8h": "funding_8h"}).sort_index()
    return _cached("BTC_funding_1h.csv", build, refresh)


def candles(refresh_tail=False):
    """The cached 3-year 15-minute BTC history, extended to now from Deribit's
    perpetual chart data (cached separately, so the original 3-year file the
    other studies share is never rewritten)."""
    base = bt.fetch_history("BTC", years=3, use_cache=True)

    def build():
        start = base.index.max().tz_convert("UTC").tz_localize(None)
        stop = pd.Timestamp.utcnow().tz_localize(None)
        frames, t = [], start
        while t < stop:
            nxt = min(t + pd.Timedelta(days=10), stop)
            res = _get("get_tradingview_chart_data", instrument_name="BTC-PERPETUAL",
                       start_timestamp=_ms(t), end_timestamp=_ms(nxt), resolution=15)
            if res.get("ticks"):
                frames.append(pd.DataFrame({"Open": res["open"], "High": res["high"], "Low": res["low"],
                                            "Close": res["close"], "Volume": res["volume"]},
                                           index=pd.to_datetime(res["ticks"], unit="ms", utc=True)))
            t = nxt
        df = pd.concat(frames)
        df.index = df.index.tz_convert(IST)
        df.index.name = "Date"
        return df[~df.index.duplicated()].sort_index()
    tail = _cached("BTC_15m_recent.csv", build, refresh_tail)
    # The 3-year cache is stamped with a fixed UTC+05:30 offset, this tail with
    # Asia/Kolkata - the same instants, but pandas will not merge two different
    # tz objects into one DatetimeIndex (it silently falls back to a plain Index).
    tail.index = tail.index.tz_convert(base.index.tz)
    tail = tail[tail.index > base.index.max()]
    # The last bar fetched may still have been forming - drop anything less
    # than 15 minutes old at fetch time is not knowable here, so the final
    # bar is always dropped to be safe.
    tail = tail.iloc[:-1] if len(tail) else tail
    out = pd.concat([base, tail])
    return out[~out.index.duplicated()].sort_index()


def short_dated_iv(every_days=7, refresh=False):
    """How short-dated BTC options ACTUALLY trade, against DVOL (a 30-day vol).

    For one hour (12:00-13:00 UTC - Delta's own settlement hour) every
    `every_days` days, real Deribit option trades from its public history host:
    near-the-money (strike within 1.5% of the index), expiring 12-36 hours out
    (the next daily). The median traded IV per sampled hour is kept, with DVOL at
    that hour beside it. A volatility-SELLING backtest priced at DVOL alone would
    be wrong by exactly the gap between these two numbers - this measures it."""
    def build():
        dv = dvol()["dvol"]
        stop = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=1)
        t = pd.Timestamp("2023-09-12 12:00")
        rows = []
        url = "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"
        while t < stop:
            q = urllib.parse.urlencode({"currency": "BTC", "kind": "option", "count": 1000,
                                        "sorting": "asc", "start_timestamp": _ms(t),
                                        "end_timestamp": _ms(t + pd.Timedelta(hours=1))})
            try:
                req = urllib.request.Request(url + "?" + q, headers={"User-Agent": "nbs-crypto-study"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    trades = json.load(r)["result"]["trades"]
            except Exception:
                trades = []
            ivs = []
            for tr in trades:
                try:
                    _, exp_s, k_s, _ = tr["instrument_name"].split("-")
                    exp = pd.Timestamp(exp_s) + pd.Timedelta(hours=8)            # Deribit expires 08:00 UTC
                    hours = (exp - pd.Timestamp(tr["timestamp"], unit="ms")).total_seconds() / 3600
                    if 12 <= hours <= 36 and abs(float(k_s) / tr["index_price"] - 1) <= 0.015 and tr.get("iv"):
                        ivs.append(float(tr["iv"]))
                except Exception:
                    continue
            at = pd.Timestamp(t, tz="UTC").tz_convert(IST)
            d = dv.asof(at)
            if len(ivs) >= 3 and d == d:
                rows.append({"at": at, "short_iv": float(pd.Series(ivs).median()), "dvol": float(d), "n": len(ivs)})
            t += pd.Timedelta(days=every_days)
            time.sleep(0.2)
        out = pd.DataFrame(rows).set_index("at")
        out["ratio"] = out["short_iv"] / out["dvol"]
        return out
    return _cached("BTC_short_iv_samples.csv", build, refresh)


if __name__ == "__main__":
    c = candles(refresh_tail=True)
    print(f"candles {len(c):,}  {c.index.min()} -> {c.index.max()}")
    v = dvol(refresh=True)
    print(f"dvol {len(v):,}  {v.index.min()} -> {v.index.max()}  median {v['dvol'].median():.1f}")
    f = funding(refresh=True)
    print(f"funding {len(f):,}  {f.index.min()} -> {f.index.max()}  median 8h {f['funding_8h'].median():.6f}")
