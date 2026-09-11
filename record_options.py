#!/usr/bin/env python3
"""
record_options.py — keep the real option prices, every trading day
================================================================================
Every premium in the backtests is a Black-Scholes estimate. That is weakest
exactly where pro_study.py found most of the profit - on the contract's own
expiry day - and it cannot be fixed from Zerodha after the fact: Kite serves
intraday history only for contracts still listed, so an expired weekly is gone
for good the morning after it settles.

So this keeps them. After each close it saves one-minute candles, with open
interest, for every strike the day could have suggested - the day's whole
range plus a margin - on the nearest two expiries of each index, plus the
near-month future, the index itself and India VIX. A few months of these files
is a real-premium dataset: a backtest can then price each trade at what the
contract actually traded at, spread and expiry-day behaviour included.

    python3 record_options.py                  # today
    python3 record_options.py --date 2026-09-10

Output: ~/trading-tool-logs/option_history/YYYY-MM-DD.csv.gz (a few MB a day).
Runs daily from launchd (com.nbs.optionrecorder) after 15:40 IST. Read-only:
it asks Zerodha for prices and places nothing.
"""
import argparse
import datetime as dt
import gzip
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import accounts          # noqa: E402
import config            # noqa: E402
import user_kite         # noqa: E402

OUT_DIR = os.path.join(os.path.expanduser("~"), "trading-tool-logs", "option_history")
INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
MARGIN_STEPS = 5          # strikes beyond the day's range on each side
EXPIRIES = 2              # nearest N expiries per index
REQ_SLEEP = 0.36          # Kite's historical API allows 3 requests a second
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _kite():
    from kiteconnect import KiteConnect
    users = accounts.always_on_users()
    token = user_kite.token_for(users[0]) if users else None
    if not token:
        raise SystemExit("No Zerodha token today - connect Zerodha in the tool, then re-run.")
    k = KiteConnect(api_key=config.KITE_API_KEY)
    k.set_access_token(token)
    return k


def _hist(k, token, day, interval="minute", oi=True):
    frm = dt.datetime.combine(day, dt.time(9, 0))
    to = dt.datetime.combine(day, dt.time(15, 45))
    for attempt in range(3):
        try:
            rows = k.historical_data(token, frm, to, interval, oi=oi)
            time.sleep(REQ_SLEEP)
            return pd.DataFrame(rows)
        except Exception as exc:
            if "Too many" in str(exc) and attempt < 2:
                time.sleep(2)
                continue
            print(f"    token {token}: {exc}")
            time.sleep(REQ_SLEEP)
            return pd.DataFrame()
    return pd.DataFrame()


def record(day):
    k = _kite()
    frames = []
    dumps = {ex: k.instruments(ex) for ex in ("NSE", "BSE", "NFO", "BFO")}

    def add(df, **tags):
        if df is None or df.empty:
            return 0
        df = df.rename(columns={"date": "ts"})
        for c, v in tags.items():
            df[c] = v
        frames.append(df)
        return len(df)

    vix = [i for i in dumps["NSE"] if i["tradingsymbol"] == "INDIA VIX"]
    if vix:
        add(_hist(k, vix[0]["instrument_token"], day, oi=False), kind="VIX", index="INDIAVIX",
            symbol="INDIA VIX", expiry=None, strike=None, opt=None)

    for key in INDICES:
        meta = config.INSTRUMENTS[key]
        ex = meta["kite_exchange"]
        dex = "NFO" if ex == "NSE" else "BFO"
        name = meta.get("nse_symbol") or key
        idx = [i for i in dumps[ex] if i["tradingsymbol"] == meta["kite_tradingsymbol"]
               and i["segment"] in ("INDICES", "BSE-INDICES")]
        if not idx:
            print(f"  {key}: index token not found"); continue
        spot = _hist(k, idx[0]["instrument_token"], day, oi=False)
        if spot.empty:
            print(f"  {key}: no candles for {day} - a holiday, or not traded yet")
            continue
        add(spot, kind="IDX", index=key, symbol=meta["kite_tradingsymbol"],
            expiry=None, strike=None, opt=None)

        derivs = [i for i in dumps[dex] if i.get("name") == name and i.get("expiry")
                  and i["expiry"] >= day]
        futs = sorted([i for i in derivs if i["instrument_type"] == "FUT"], key=lambda i: i["expiry"])
        if futs:
            f = futs[0]
            add(_hist(k, f["instrument_token"], day), kind="FUT", index=key,
                symbol=f["tradingsymbol"], expiry=str(f["expiry"]), strike=None, opt=None)

        step = meta["strike_step"]
        lo = (spot["low"].min() // step - MARGIN_STEPS) * step
        hi = (spot["high"].max() // step + 1 + MARGIN_STEPS) * step
        opts = [i for i in derivs if i["instrument_type"] in ("CE", "PE")]
        expiries = sorted({i["expiry"] for i in opts})[:EXPIRIES]
        want = [i for i in opts if i["expiry"] in expiries and lo <= i["strike"] <= hi]
        n_rows = 0
        for i in sorted(want, key=lambda i: (i["expiry"], i["strike"], i["instrument_type"])):
            n_rows += add(_hist(k, i["instrument_token"], day), kind="OPT", index=key,
                          symbol=i["tradingsymbol"], expiry=str(i["expiry"]),
                          strike=i["strike"], opt=i["instrument_type"])
        print(f"  {key}: {len(want)} contracts on {', '.join(map(str, expiries))}, "
              f"strikes {lo:,.0f}-{hi:,.0f}, {n_rows:,} option candles")

    if not frames:
        print("Nothing recorded."); return None
    out = pd.concat(frames, ignore_index=True)
    cols = ["ts", "kind", "index", "symbol", "expiry", "strike", "opt",
            "open", "high", "low", "close", "volume", "oi"]
    out = out[[c for c in cols if c in out.columns]]
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{day}.csv.gz")
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt") as fh:
        out.to_csv(fh, index=False)
    os.replace(tmp, path)
    print(f"Saved {len(out):,} rows -> {path} ({os.path.getsize(path) / 1e6:.1f} MB)")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, IST)")
    ap.add_argument("--wait", action="store_true",
                    help="if run before 15:40 IST, wait for the close first")
    args = ap.parse_args()
    now = dt.datetime.now(IST)
    day = dt.date.fromisoformat(args.date) if args.date else now.date()
    if args.wait and day == now.date():
        close = now.replace(hour=15, minute=41, second=0, microsecond=0)
        if now < close:
            time.sleep((close - now).total_seconds())
    if day.weekday() >= 5:
        print(f"{day} is a weekend."); return
    print(f"Recording option prices for {day} ({dt.datetime.now(IST):%H:%M} IST)")
    record(day)


if __name__ == "__main__":
    main()
