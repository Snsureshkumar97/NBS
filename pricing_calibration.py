#!/usr/bin/env python3
"""
pricing_calibration.py — how far the studies' option model is from the real option prices, and its correction
================================================================================
The user, 28 Oct 2026: "yes fix the pricing model". live_vs_backtest.py priced 165 TradingView-rating backtest trades
with the recorder's real option prices: the model (Black-Scholes on yesterday's VIX scaled to the index, calendar time
to 15:30 on expiry day) was -296 a trade too rosy, almost all of it close to expiry (under 1 day -1,029, 1-3 days
-368, 3-7 -249, 7+ -27): real premiums lose value faster than it says.

THE MEASURE: at every 5th recorded minute, the at-the-money call and put of the nearest two expiries, each index - the
volatility their real price implies (Black-Scholes, the model's own time) over the model's sigma for that day. The
correction is that ratio, the median in each cell of (time to expiry) x (time of day), applied to the model's sigma
at the moment it prices (entry and exit alike) - pricing.premium().

HONEST CHECK, FIXED BEFORE ANY RESULT: the table is learned on the recorded days to 9 Oct 2026 and judged on the
TradingView rating's backtest trades from 10 Oct (which it never saw): the gap per trade, real minus corrected model,
overall and by days to expiry, against the old model's. Then the table is re-learned on every recorded day for use.

RESULT (28 Oct 2026, 10 Sep - 26 Oct; pricing_calibration_results.txt). Real at-the-money implied volatility over the
model's sigma: expiry day about 2x (calendar clock), 1-2 days 1.27, 2-3 days 1.10, 3-5 days 1.00, then 0.92-0.95.
Held out (learned to 9 Oct, 71 trades after): the gap per trade -376 -> -165 (expiry day -957 -> +207, 7+ days -2 ->
+64; 1-7 days still -300 to -370: week-to-week volatility, not a fixed bias). Every clock tried within a few rupees;
the rule picked ("trading", 0.2). Over all 165 trades with the table learned on every day: -296 -> -69. In pricing.py.
RE-RUN (29 Oct 2026) with clock_gains: far_expiry_check.py found the 14+ day row (0.872 rising to 0.919 through the
day) let a far option gain from the clock alone - Bank Nifty's real far trades were +296 a trade rosier in the model.
fit() now gives such a row one ratio all day (an option held 2+ hours must not gain over 1% from the clock). Re-learned,
the rule picked the calendar clock (learning trades +37 a trade, the trading clocks +64 to +133); held out -160 a
trade (se 93; the 28 Oct table -165); every recorded day -49 (old -296). The 14+ day row is 0.92 all day.

    python3 pricing_calibration.py OPTION_HISTORY_DIR      (HOME at the six-year history)
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
IST = "Asia/Kolkata"
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
SPLIT = pd.Timestamp("2026-10-10", tz=IST)


def implied_vol(price, S, K, T, call):
    """Black-Scholes implied volatility by bisection; None when the price is outside what any volatility gives."""
    import regime_study as rs
    lo, hi = 0.01, 3.0
    intrinsic = max(0.0, (S - K) if call else (K - S))
    if price <= intrinsic + 0.05 or price >= rs.bs(S, K, T, hi, call):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if rs.bs(S, K, T, mid, call) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def sigma_by_day(k):
    """The studies' sigma for each day (regime_study.features: yesterday's VIX scaled to the index)."""
    import backtest_intraday as bt
    import regime_study as rs
    df = bt.fetch_history(k, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"]
    return sig.groupby(sig.index.date).first().to_dict()


def samples(folder, every=5, time=("calendar",)):
    """[{index, ts, dte (days, calendar, to 15:30 on expiry), tod (minutes since 09:15), ratio}]"""
    import config
    import live_vs_backtest as lv
    import pricing
    import pro_study as ps
    P = lv.Prices(folder)
    days = sorted(f[:10] for f in os.listdir(folder) if f.endswith(".csv.gz"))
    sig = {k: sigma_by_day(k) for k in KEYS}
    out = []
    for d in days:
        x = P.day(d)
        if x is None:
            continue
        for k in KEYS:
            s = sig[k].get(pd.Timestamp(d).date())
            if not s or s != s:
                continue
            step = config.INSTRUMENTS[k]["strike_step"]
            idx = P.index(k, d)
            if idx is None or idx.empty:
                continue
            opts = x[(x["kind"] == "OPT") & (x["index"] == k)]
            for exp in sorted(e for e in opts["expiry"].dropna().unique() if e >= d)[:2]:
                ox = opts[opts["expiry"] == exp].set_index(["strike", "opt", "ts"])["close"]
                expd = pd.Timestamp(exp).date()
                for ts in idx.index[::every]:
                    S = float(idx.loc[ts, "close"])
                    K = round(S / step) * step
                    T = pricing.years(expd, ts, time)
                    for opt in ("CE", "PE"):
                        try:
                            p = float(ox.loc[(float(K), opt, ts)])
                        except KeyError:
                            continue
                        iv = implied_vol(p, S, K, T, opt == "CE")
                        if iv:
                            out.append({"index": k, "ts": ts, "dte": ps.years_to(expd, ts) * 365,
                                        "tod": (ts.hour * 60 + ts.minute) - 555,
                                        "ratio": iv / s})
    return pd.DataFrame(out)


from pricing import DTE_EDGES, TOD_EDGES   # noqa: E402  (the cells the correction is kept in)


def fit(df, time=None):
    """{(dte cell, tod cell): median ratio} - a cell with under 30 readings takes its dte row's median, and a dte row
    whose times of day would let an option gain from the clock alone (clock_gains) takes its row median all day."""
    df = df.assign(dc=pd.cut(df["dte"], DTE_EDGES, labels=False, right=False),
                   tc=pd.cut(df["tod"], TOD_EDGES, labels=False, right=False))
    row = df.groupby("dc")["ratio"].median().to_dict()
    table = {}
    for dc in range(len(DTE_EDGES) - 1):
        for tc in range(len(TOD_EDGES) - 1):
            cell = df[(df["dc"] == dc) & (df["tc"] == tc)]["ratio"]
            table[(dc, tc)] = round(float(cell.median()), 3) if len(cell) >= 30 else round(float(row.get(dc, 1.0)), 3)
    for dc in sorted(clock_gains(table, time)):
        for tc in range(len(TOD_EDGES) - 1):
            table[(dc, tc)] = round(float(row.get(dc, 1.0)), 3)
    return table


def clock_gains(table, time=None, hold=120, tol=0.01):
    """The dte cells in which an at-the-money option, the index unchanged, can end a hold of `hold` minutes or more
    worth over `tol` more than it started - value from the clock alone, which no real option has. far_expiry_check.py
    (29 Oct 2026) found it in the 14+ day cell (0.872 rising to 0.919 through the day): a 35-day option gained Rs 1,009
    a lot from 09:45 to 14:45, +4.8% over two hours, and the six-year 14+ day result was mostly that; real prices
    showed the model +296 a trade too rosy there. The nearer cells blip up for an hour at the table's time-of-day
    steps (+3% at most) but lose over two hours, and real prices confirm them (+48 a trade, se 88, on the same trades
    at the nearest expiry). Checked over 0-60 days to expiry, from every weekday, every 5 minutes."""
    import datetime as dt
    import pricing
    import pro_study as ps
    bad = set()
    for start in range(5):
        day = dt.date(2026, 10, 5) + dt.timedelta(days=start)
        open_ = pd.Timestamp(dt.datetime.combine(day, dt.time(9, 15)), tz=IST)
        whens = [open_ + pd.Timedelta(minutes=m) for m in range(5, 371, 5)]
        for ahead in range(61):
            exp = day + dt.timedelta(days=ahead)
            px = [pricing.premium(24000.0, 24000, exp, w, 0.13, True, table=table, time=time) for w in whens]
            for i, w in enumerate(whens):
                later = px[i + hold // 5:]
                if later and max(later) > px[i] * (1 + tol):
                    bad.add(pricing._cell(ps.years_to(exp, w) * 365, DTE_EDGES))
    return bad


def backtest_trades():
    import multiprocessing as mp
    import live_vs_backtest as lv
    with mp.get_context("spawn").Pool(processes=4) as pool:
        return dict(pool.map(lv._tv4, KEYS))


def judge(table, folder, since, until, bt, time=("calendar",)):
    """The TradingView backtest trades in [since, until): real - model per trade, the old model and the corrected."""
    import config
    import live_vs_backtest as lv
    import pricing
    import pro_study as ps
    import regime_study as rs
    P = lv.Prices(folder)
    rows = []
    for k in KEYS:
        qty, step = config.INSTRUMENTS[k]["lot_size"], config.INSTRUMENTS[k]["strike_step"]
        for r in bt[k]:
            if not (since <= pd.Timestamp(r["when"]) < until):
                continue
            real = lv.bt_real(r, P)
            if real is None:
                continue
            exp = pd.Timestamp(real[1]).date()
            K = round(r["entry_spot"] / step) * step
            call = r["side"] == "CE"
            old = (rs.bs(r["exit_spot"], K, ps.years_to(exp, r["exit_time"]), r["sigma"], call)
                   - rs.bs(r["entry_spot"], K, ps.years_to(exp, r["when"]), r["sigma"], call)) * qty
            new = (pricing.premium(r["exit_spot"], K, exp, r["exit_time"], r["sigma"], call, table, time)
                   - pricing.premium(r["entry_spot"], K, exp, r["when"], r["sigma"], call, table, time)) * qty
            rows.append({"dte": ps.years_to(exp, r["when"]) * 365, "real": real[0], "old": old, "new": new})
    return pd.DataFrame(rows)


GRID = (("calendar",), ("trading", 0.0), ("trading", 0.1), ("trading", 0.2), ("trading", 0.35), ("trading", 0.5))
FIRST = pd.Timestamp("2026-09-10", tz=IST)
LAST = pd.Timestamp("2026-10-27", tz=IST)


def main():
    folder = sys.argv[1]
    bt = backtest_trades()
    print("THE CLOCK, picked on the recorded days to 9 Oct (table AND trades), then judged on the trades from 10 Oct:")
    best, results = None, {}
    for time in GRID:
        S = samples(folder, every=10, time=time)
        learn = fit(S[S["ts"] < SPLIT], time)
        L = judge(learn, folder, FIRST, SPLIT, bt, time)
        H = judge(learn, folder, SPLIT, LAST, bt, time)
        gl, gh = (L["real"] - L["new"]).mean(), (H["real"] - H["new"]).mean()
        results[time] = (S, H)
        print(f"   {str(time):18s} learning trades ({len(L)}): gap {gl:+6,.0f} a trade   |   held-out ({len(H)}): gap "
              f"{gh:+6,.0f} a trade (se {(H['real'] - H['new']).std() / len(H) ** .5:,.0f})")
        if best is None or abs(gl) < best[1]:
            best = (time, abs(gl))
    time = best[0]
    S, H = results[time]
    print(f"PICKED ON THE LEARNING TRADES: {time}")
    H["dc"] = pd.cut(H["dte"], (0, 1, 3, 7, 400), right=False)
    g = H.assign(old_gap=H["real"] - H["old"], new_gap=H["real"] - H["new"]).groupby("dc", observed=True)
    print(g[["old_gap", "new_gap"]].agg(["count", "mean"]).round(0).to_string())
    print(f"   held-out {len(H)} trades: real {H['real'].sum():+,.0f}, old model {H['old'].sum():+,.0f} "
          f"(gap {(H['real'] - H['old']).mean():+,.0f} a trade), corrected {H['new'].sum():+,.0f} "
          f"(gap {(H['real'] - H['new']).mean():+,.0f} a trade)")
    full = fit(S, time)
    A = judge(full, folder, FIRST, LAST, bt, time)
    print(f"EVERY RECORDED DAY ({len(A)} trades) with the table re-learned on all of them: gap "
          f"{(A['real'] - A['new']).mean():+,.0f} a trade (old {(A['real'] - A['old']).mean():+,.0f})")
    print(f"THE CLOCK FOR USE - pricing.TIME = {time}")
    print("THE TABLE FOR USE - pricing.SIGMA_RATIO:")
    print("{" + ", ".join(f"{k}: {v}" for k, v in full.items()) + "}")


if __name__ == "__main__":
    main()
