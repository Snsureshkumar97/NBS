#!/usr/bin/env python3
"""
stock_options_study.py — pick the day's strongest stock after the open and buy its option (3 years)
================================================================================
The user, 14 Oct 2026: "can my tool pick high performance stock after the market open and do option trading in that
can we back test that and see how are the results".

DATA: 15-minute candles of the 56 market-map stocks (Nifty 50 + Bank Nifty + Sensex members, all in F&O), 1 Sep 2023
to 14 Oct 2026 (~/trading-tool-logs/history/stocks, fetched for breadth_study.py). TODAY's member lists, lot sizes and
strike steps are used for all three years (survivorship bias: stocks that fell out of the index are missing).

OPTION PRICES ARE A MODEL. Zerodha keeps no prices for expired stock options, so each trade is priced with
Black-Scholes at the money on the stock's monthly expiry (last Thursday, last Tuesday from Sep 2025; the next month on
expiry day), volatility = 1.07 x the stock's previous 20-day realised volatility. The 1.07 is the median ratio of
implied to realised volatility across the 56 stocks' real at-the-money options at the close on 14 Oct 2026 (range
0.54-1.68, so single trades are priced roughly). The model does not see results-day volatility crush or a stock
option's real spread; slippage is charged instead: 0.5% of the premium each way (base), and 1% each way as a stress
test - the real at-the-money spread at the 14 Oct close had a median of 2.0% (75th percentile 2.9%), wider than an
index option's ~0.5%. Every Zerodha charge, one lot per trade (today's lot), no compounding.

THE RULE (fixed before any result was seen, 14 Oct 2026). Each trading day, at the close of the first 15-minute candle
(09:30):
  * rank the 56 stocks by their move from yesterday's close (a "top gainers / top losers" list)
  * BUY THE CALL of the biggest gainer if it is up at least 1%; BUY THE PUT of the biggest loser if it is down at
    least 1% (so 0, 1 or 2 trades a day)
  * stop: the first candle's low for a call / high for a put (at least 0.5% from the entry); checked first in every
    candle (the worse reading when both are touched)
  * otherwise out at 15:15
MAIN = that rule at 0.5% slippage. The VERDICT is on MAIN only: worth building if it makes money after costs in BOTH
the first two years (to 15 Aug 2025) and the held-out year, and still does at 1% slippage. The other lines are shown to
see which part of the rule matters, not to pick a winner afterwards:
  NO_STOP    hold to 15:15 whatever happens
  TARGET_2R  MAIN plus a target at twice the risk
  FIRST_HOUR pick at 10:15 (the first hour's leader), stop at the day's low / high so far
  FROM_OPEN  rank by the move since today's open (today's strength without the overnight gap)
  LIQUID     MAIN on the 23 stocks whose at-the-money call and put both had a spread of 2% or less on 14 Oct
  SLIP_1PCT  MAIN at 1% slippage each way (the stress test)
And without any option at all: how far the picked stock itself moved from the entry to the exit, in % - whether the
morning's leaders keep going (what a bought option needs) or give it back.

RESULT (14 Oct 2026; stock_options_study_results.txt) - NOT BUILT (the user's interest is index options):
  MAIN +352,827 / +179,135 (PF 1.23 / 1.22), at 1% slippage +232,279 / +102,119 - the pre-set verdict says PASS.
  But the stock itself goes nowhere: the same picks traded as the stock / futures lose -0.071% a trade in the first
  two years and -0.004% in the held-out year after costs; the leader keeps going only 39-48% of the time. The option
  profit is convexity bought at a model volatility: with the mover's options 50% dearer than a normal day's (likely on
  news days, and unmeasurable without real stock option history) it loses at 1% slippage. The calendar clock itself
  checked out on index options (decay_clock_check.py); the blend lines below are too harsh. Unproven: only a paper
  forward test at REAL stock option prices could settle it.

    /usr/bin/python3 stock_options_study.py
"""
import datetime as dt
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

STOCKS = os.path.expanduser("~/trading-tool-logs/history/stocks")
IV_OVER_RV = 1.07
# Today's (14 Oct 2026) F&O lot size and strike step of each stock, from Kite's NFO instrument list.
CONTRACTS = {"ADANIENT": (309, 50), "ADANIPORTS": (475, 20), "APOLLOHOSP": (125, 100), "ASIANPAINT": (250, 20),
             "AUBANK": (1000, 10), "AXISBANK": (625, 10), "BAJAJ-AUTO": (75, 100), "BAJAJFINSV": (300, 20),
             "BAJFINANCE": (750, 10), "BANKBARODA": (2925, 2.5), "BEL": (1425, 5), "BHARTIARTL": (475, 20),
             "BPCL": (1975, 5), "BRITANNIA": (125, 50), "CANBK": (6750, 1), "CIPLA": (425, 10),
             "COALINDIA": (1350, 5), "DRREDDY": (625, 10), "EICHERMOT": (100, 100), "ETERNAL": (2425, 5),
             "FEDERALBNK": (2500, 5), "GRASIM": (250, 20), "HCLTECH": (400, 10), "HDFCBANK": (650, 10),
             "HDFCLIFE": (1100, 5), "HEROMOTOCO": (150, 50), "HINDALCO": (700, 10), "HINDUNILVR": (300, 20),
             "ICICIBANK": (700, 10), "IDFCFIRSTB": (9275, 1), "INDUSINDBK": (700, 10), "INFY": (400, 20),
             "ITC": (1725, 2.5), "JSWSTEEL": (675, 10), "KOTAKBANK": (2000, 5), "LT": (175, 50), "M&M": (200, 50),
             "MARUTI": (50, 100), "NESTLEIND": (500, 10), "NTPC": (1500, 2.5), "ONGC": (2250, 2.5), "PNB": (8000, 1),
             "POWERGRID": (1900, 2.5), "RELIANCE": (500, 10), "SBILIFE": (375, 20), "SBIN": (750, 10),
             "SHRIRAMFIN": (825, 20), "SUNPHARMA": (350, 20), "TATACONSUM": (550, 10), "TATASTEEL": (2750, 2.5),
             "TCS": (225, 20), "TECHM": (600, 20), "TITAN": (175, 50), "TRENT": (225, 50), "ULTRACEMCO": (50, 100),
             "WIPRO": (3000, 2.5)}
LIQUID = ("ADANIENT", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BANKBARODA", "CANBK", "DRREDDY", "HCLTECH", "HINDALCO",
          "ICICIBANK", "INFY", "LT", "M&M", "MARUTI", "NESTLEIND", "RELIANCE", "SBIN", "SHRIRAMFIN", "TATACONSUM",
          "TATASTEEL", "TCS", "TECHM", "TRENT")
VARIANTS = {   # name: (pick candle start, rank from, stop, target in R, universe, slippage each way)
    "MAIN": ("09:15", "prev_close", True, None, None, 0.005),
    "NO_STOP": ("09:15", "prev_close", False, None, None, 0.005),
    "TARGET_2R": ("09:15", "prev_close", True, 2.0, None, 0.005),
    "FIRST_HOUR": ("10:00", "prev_close", True, None, None, 0.005),
    "FROM_OPEN": ("09:15", "open", True, None, None, 0.005),
    "LIQUID": ("09:15", "prev_close", True, None, LIQUID, 0.005),
    "SLIP_1PCT": ("09:15", "prev_close", True, None, None, 0.01),
}
MIN_MOVE = 0.01          # the leader must be up / down at least 1% to be traded
MIN_STOP = 0.005         # the stop at least 0.5% from the entry
EXIT_AT = "15:00"        # out at the close of the 15:00 candle, 15:15


def load():
    """{symbol: {date: that day's candles}}, the trading days, and each stock's previous close and the volatility
    known before each day's open."""
    by_day, closes = {}, {}
    for sym in CONTRACTS:
        df = pd.read_csv(os.path.join(STOCKS, f"{sym}_15m.csv"), index_col=0, parse_dates=True)
        df["hm"] = df.index.strftime("%H:%M")
        g = dict(tuple(df.groupby(df.index.date)))
        by_day[sym] = g
        closes[sym] = df["Close"].groupby(df.index.date).last()
    daily = pd.DataFrame(closes).sort_index()
    rv20 = (np.log(daily).diff().rolling(20).std() * math.sqrt(252)).shift(1)    # to yesterday's close
    return by_day, sorted(daily.index), daily.shift(1), rv20


def monthly_expiry(d, days):
    """The stock options' monthly expiry on or after d (the last Thursday, the last Tuesday from Sep 2025; a
    holiday moves it to the trading day before); on the expiry day itself, the next month's."""
    import pro_study as ps
    last = days[-1]
    for k in range(0, 4):
        y, m = d.year + (d.month + k - 1) // 12, (d.month + k - 1) % 12 + 1
        e = ps._last_weekday(y, m, 1 if dt.date(y, m, 1) >= dt.date(2025, 9, 1) else 3)
        while e <= last and e not in days_set(days):
            e -= dt.timedelta(days=1)
        if e > d:
            return e
    return d


_DS = {}


def days_set(days):
    key = id(days)
    if key not in _DS:
        _DS[key] = set(days)
    return _DS[key]


def run(name, data):
    import pro_study as ps
    import regime_study as rs
    pick_at, rank_from, use_stop, target_r, universe, slip = VARIANTS[name]
    by_day, days, prev, rv20 = data
    rows = []
    for d in days:
        cand = []
        for sym in (universe or CONTRACTS):
            c = by_day[sym].get(d)
            pc = prev.at[d, sym]
            if c is None or not (pc == pc):
                continue
            upto = c[c["hm"] <= pick_at]
            if not len(upto) or upto["hm"].iloc[-1] != pick_at:
                continue
            entry = float(upto["Close"].iloc[-1])
            base = pc if rank_from == "prev_close" else float(upto["Open"].iloc[0])
            cand.append((entry / base - 1, sym, entry, float(upto["Low"].min()), float(upto["High"].max()), c))
        if not cand:
            continue
        cand.sort(key=lambda x: x[0])
        picks = []
        if cand[-1][0] >= MIN_MOVE:
            picks.append(("CE", cand[-1]))
        if cand[0][0] <= -MIN_MOVE:
            picks.append(("PE", cand[0]))
        for side, (move, sym, entry, lo, hi, c) in picks:
            ce = side == "CE"
            stop = min(lo, entry * (1 - MIN_STOP)) if ce else max(hi, entry * (1 + MIN_STOP))
            risk = abs(entry - stop)
            target = (entry + target_r * risk if ce else entry - target_r * risk) if target_r else None
            after = c[(c["hm"] > pick_at) & (c["hm"] <= EXIT_AT)]
            if not len(after):
                continue
            exit_px, exit_t, how = None, None, "close"
            for t, bar in after.iterrows():
                if use_stop and ((bar["Low"] <= stop) if ce else (bar["High"] >= stop)):
                    exit_px, exit_t, how = stop, t, "stop"
                    break
                if target is not None and ((bar["High"] >= target) if ce else (bar["Low"] <= target)):
                    exit_px, exit_t, how = target, t, "target"
                    break
            if exit_px is None:
                exit_px, exit_t = float(after["Close"].iloc[-1]), after.index[-1]
            sigma = rv20.at[d, sym] * IV_OVER_RV
            if not (sigma == sigma) or sigma <= 0:
                continue
            lot, step = CONTRACTS[sym]
            K = round(entry / step) * step
            exp = monthly_expiry(d, days)
            t_in = upto.index[-1] + pd.Timedelta(minutes=15)
            t_out = exit_t + pd.Timedelta(minutes=15)
            p0 = rs.bs(entry, K, ps.years_to(exp, t_in), sigma, ce)
            if p0 <= 0.5:
                continue
            p1 = rs.bs(exit_px, K, ps.years_to(exp, t_out), sigma, ce)
            net = rs.net_rupees(p0, p1, lot, "NSE", slip)
            rows.append({"when": t_in, "net": net, "side": side, "sym": sym, "how": how,
                         "stock_move": (exit_px / entry - 1) * (1 if ce else -1) * 100, "premium": p0 * lot,
                         "leader_move": move * 100})
    return rows


def main():
    import pro_study as ps
    import stop_day_study as sds
    data = load()
    got = {v: run(v, data) for v in VARIANTS}
    W = 172
    print("=" * W)
    print(" BUY THE OPTION OF THE DAY'S STRONGEST / WEAKEST STOCK AFTER THE OPEN - 56 F&O stocks, 15-minute, option"
          " prices from a model (see the docstring). Per lot, after costs.")
    print("  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR (to 14 Oct 2026)")
    for v in VARIANTS:
        print(sds.line("   " + v, got[v]))
        for side in ("CE", "PE"):
            print(sds.line(f"      {v} {side} only", [r for r in got[v] if r["side"] == side]))
    print("\n THE STOCK ITSELF, from the entry to the exit (in the trade's direction), MAIN's picks - does the morning's"
          " leader keep going?")
    for p, sel in (("first two years", [r for r in got["MAIN"] if r["when"] < sds.SPLIT]),
                   ("held-out year", [r for r in got["MAIN"] if r["when"] >= sds.SPLIT])):
        for side in ("CE", "PE"):
            m = np.array([r["stock_move"] for r in sel if r["side"] == side])
            hw = pd.Series([r["how"] for r in sel if r["side"] == side]).value_counts().to_dict()
            if len(m):
                print(f"   {p:16s} {side}: {len(m):4d} trades, stock moved {m.mean():+.2f}% on average (median"
                      f" {np.median(m):+.2f}%), further the right way on {(m > 0).mean() * 100:.0f}%  exits {hw}")
    prem = np.median([r["premium"] for r in got["MAIN"]])
    print(f"\n MAIN's median premium per lot at entry: Rs {prem:,.0f}")
    print("\n VERDICT (MAIN only, fixed beforehand): money after costs in BOTH periods, and still at 1% slippage")
    s, t = sds.split(got["MAIN"]), sds.split(got["SLIP_1PCT"])
    ok = s["is"]["total"] > 0 and s["oos"]["total"] > 0 and t["is"]["total"] > 0 and t["oos"]["total"] > 0
    print(f"   {'PASS' if ok else 'FAIL'}  MAIN {s['is']['total']:+,.0f} / {s['oos']['total']:+,.0f}"
          f"   at 1% slippage {t['is']['total']:+,.0f} / {t['oos']['total']:+,.0f}")
    checks(data, got["MAIN"])
    return got


# ADDED AFTER THE FIRST RUN (14 Oct 2026): MAIN "passed" while the picked stocks themselves went nowhere, so the profit
# had to come from the option model. These checks measure how much of it is the model.
CAL_WEIGHT = 3.1 / (3.1 + 2.7)   # decay_clock_check.py: real index straddles' implied vol 09:30 -> 15:15 moves -2.7%
                                 # (median) on the calendar clock and +3.1% on the trading clock - real prices decay
                                 # between the two; this blend keeps it level


def trading_years_to(days):
    """Time to expiry on the TRADING clock: sessions after today up to expiry, plus what is left of today's 09:15-15:30
    session, 252 a year (the realised volatility is annualised on 252 days too)."""
    import bisect

    def f(expiry_date, when):
        d = when.date()
        t = when.tz_convert("Asia/Kolkata").replace(tzinfo=None).to_pydatetime()
        left = max(0.0, (dt.datetime.combine(d, dt.time(15, 30)) - t).total_seconds()) / (6.25 * 3600)
        n = (bisect.bisect_right(days, expiry_date) - bisect.bisect_right(days, d)) if expiry_date <= days[-1] \
            else int(np.busday_count(d + dt.timedelta(days=1), expiry_date + dt.timedelta(days=1)))
        return max((n + left) / 252, 1e-6)
    return f


def checks(data, main_rows):
    import pro_study as ps
    import stop_day_study as sds
    global IV_OVER_RV
    print("\n" + "=" * 172)
    print(" CHECKS ADDED AFTER THE FIRST RUN - how much of MAIN's profit is the option model")
    print("=" * 172)
    print(" 1. THE SAME RULE ON THE STOCK ITSELF (no option; ~0.06% round trip, like futures):")
    for p, sel in (("first two years", [r for r in main_rows if r["when"] < sds.SPLIT]),
                   ("held-out year", [r for r in main_rows if r["when"] >= sds.SPLIT])):
        m = np.array([r["stock_move"] for r in sel]) - 0.06
        print(f"    {p:16s} {len(m)} trades, {m.mean():+.3f}% a trade after costs, {(m > 0).mean() * 100:.0f}% won")
    cal, base_iv, trd = ps.years_to, IV_OVER_RV, trading_years_to(data[1])
    clocks = (("calendar clock (the studies' own)", cal), ("trading clock", trd),
              (f"real-price blend ({CAL_WEIGHT:.2f} calendar)", lambda e, w: CAL_WEIGHT * cal(e, w) + (1 - CAL_WEIGHT) * trd(e, w)))
    print(" 2. MAIN / 1% slippage under each time-decay clock and with the picked stock's options dearer than a normal"
          " day (a mover's volatility is usually bid up):")
    try:
        for cname, clock in clocks:
            ps.years_to = clock
            for m in (1.0, 1.25, 1.5):
                IV_OVER_RV = base_iv * m
                a, b = sds.split(run("MAIN", data)), sds.split(run("SLIP_1PCT", data))
                print(f"    {cname:34s} vol x{m:.2f}   MAIN {a['is']['total']:>+10,.0f} PF {a['is']['pf']:.2f} / "
                      f"{a['oos']['total']:>+10,.0f} PF {a['oos']['pf']:.2f}    1% slippage {b['is']['total']:>+10,.0f} / "
                      f"{b['oos']['total']:>+10,.0f}")
    finally:
        ps.years_to, IV_OVER_RV = cal, base_iv


if __name__ == "__main__":
    main()
