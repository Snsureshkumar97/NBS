#!/usr/bin/env python3
"""
entry_timing_study.py — enter the moment the signal turns on (live) vs at the candle's close vs on confirmation
================================================================================
The user, 6 Oct 2026: "the tool in entering with the rules but i have noticed its enter option prices sometime at high
price". Real fills since 28 Sep: 8 of 17 in the top 16% of their minute, opening entries 18-113% above the option's
5-minute low. Live reads the FORMING 15-minute candle every second and fires the first moment every vote agrees - often
mid-surge. Every backtest so far entered only at a candle's CLOSE, so it never saw that. "yes fix it and run the test".

ON 3 YEARS OF 5-MINUTE INDEX CANDLES (~/trading-tool-logs/history/<INDEX>_5m_3y.csv) each 15-minute candle is read
three times as it forms - after 5 minutes, after 10, and at its close - through the engine's own code (precompute /
tech_at / compute_reachability / build_recommendation, then the live entry checks, reversal_exit_study.live_gate) on a
150-candle window (live keeps about 5 days; every variant uses the same window):
  first signal   enter at the first read that says so - what live does (to the nearest 5 minutes)
  candle close   enter only at a 15-minute close - what the backtests assumed
  confirmed      enter only when two reads in a row agree (5 minutes of agreement)
Same exits for all (live, on the 5-minute candles): the stop; T1 -> the stop to T1, then the 15-minute Supertrend
trail; out at EXIT_AT_TARGET; the 2-hour breakeven filled at the market when already past; the day's last candle. One
position at a time per index, 20 minutes before re-entering the same way. Options priced as pro_study.price() does
(real expiries, Black-Scholes on the day's sigma, slippage and Zerodha's charges), per lot. In-sample to 15 Aug 2025,
then held-out; a variant is KEPT only if it beats "first signal" (today's live behaviour) in BOTH periods.

LIMIT: Black-Scholes on a daily sigma does not see the ask widening or volatility jumping inside a spike, so the
backtest UNDER-charges buying mid-surge - a confirmation rule's real advantage is if anything larger than shown.

    python3 entry_timing_study.py
"""
import datetime as dt
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
WINDOW = 150
WAIT_BARS_5M = 24                      # TIME_BREAKEVEN_MINUTES = 120 on 5-minute candles
COOLDOWN = pd.Timedelta(minutes=20)


def load_5m(key):
    p = os.path.expanduser(f"~/trading-tool-logs/history/{key}_5m_3y.csv")
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    df.index = pd.DatetimeIndex(df.index).tz_convert("Asia/Kolkata")
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def partial_rows(df15, df5):
    """{15-minute bar start: [OHLCV after 5 min, after 10 min]} from its own 5-minute candles."""
    out = {}
    for t, g in df5.groupby(df5.index.floor("15min")):
        if len(g) < 2:
            continue
        rows = []
        for k in (1, 2):
            s = g.iloc[:k]
            rows.append((float(s["Open"].iloc[0]), float(s["High"].max()), float(s["Low"].min()),
                         float(s["Close"].iloc[-1]), float(s["Volume"].sum())))
        out[t] = rows
    return out


def read(key, window, now):
    """The engine's own reading of the window's LAST candle (complete or forming), through the live entry checks.
    None when it would not enter, else (side, entry spot, stop, t1, t2, t3, adx)."""
    import backtest_intraday as bt
    import signal_engine as se
    import reversal_exit_study as res
    pre = bt.precompute(window, key)
    n = len(window) - 1
    tech = bt.tech_at(window, pre, n)
    if not np.isfinite(tech["last_atr"]) or tech["last_atr"] <= 0 or not np.isfinite(pre["typical"].iloc[n]):
        return None
    no_chain = se.compute_option_chain_signal(None)
    reach = se.compute_reachability(
        tech["last_close"], no_chain, window, now.to_pydatetime(), adx=tech["adx"],
        range_stats=(float(pre["typical"].iloc[n]), float(pre["used_today"].iloc[n])), index_key=key,
        day_extremes=(float(pre["day_high"].iloc[n]), float(pre["day_low"].iloc[n])))
    rec = se.build_recommendation(key, tech, no_chain, config.INSTRUMENTS[key]["strike_step"], reach=reach)
    if rec["bias"] == "NEUTRAL" or rec["index_stop_loss"] is None or rec["index_targets"][0] is None:
        return None
    if not res.live_gate(key, n, rec, window):
        return None
    t1, t2, t3 = rec["index_targets"]
    return (rec["option_type"], float(rec["spot"]), float(rec["index_stop_loss"]), t1, t2, t3, float(tech["adx"]))


def signals_for(args):
    """Every read of every 15-minute candle of one index: [(time, bar index, k, reading or None)]."""
    key, = args
    import backtest_intraday as bt
    df15 = bt.fetch_history(key, years=3, use_cache=True)
    parts = partial_rows(df15, load_5m(key))
    cols = ["Open", "High", "Low", "Close", "Volume"]
    out = []
    for i in range(WINDOW, len(df15)):
        t = df15.index[i]
        base = df15.iloc[i - WINDOW:i]
        rows = parts.get(t)
        for k in (1, 2, 3):
            if k < 3:
                if rows is None:
                    continue
                last = pd.DataFrame([rows[k - 1]], columns=cols, index=[t])
            else:
                last = df15.iloc[i:i + 1][cols]
            w = pd.concat([base[cols], last])
            now = t + pd.Timedelta(minutes=5 * k)
            try:
                r = read(key, w, now)
            except Exception:
                r = None
            out.append((now, i, k, r))
    return key, out


def select(sigs, variant):
    """The candidate entries one variant takes: [(time, bar index, k, reading)] in time order."""
    out = []
    prev = None
    for now, i, k, r in sigs:
        ok = r is not None
        if variant == "candle close" and k != 3:
            ok = False
        if variant == "confirmed":
            ok = ok and prev is not None and prev[3] is not None and prev[3][0] == r[0] and \
                (now - prev[0]) <= pd.Timedelta(minutes=5)
        if ok:
            out.append((now, i, k, r))
        prev = (now, i, k, r)
    return out


def simulate(df5, st15_at, entry_time, r, target):
    """Live exit on 5-minute candles from entry_time: [(exit spot, exit time)]."""
    side, entry, stop, t1, t2, t3 = r[:6]
    ce = side == "CE"
    tg = {"t1": t1, "t2": t2, "t3": t3}[target]
    pos = df5.index.searchsorted(entry_time)
    day = entry_time.date()
    t1_done = be_done = False
    n = 0
    for j in range(pos, len(df5)):
        ts = df5.index[j]
        if ts.date() != day:
            prev = df5.index[j - 1]
            return [(float(df5["Close"].iloc[j - 1]), prev + pd.Timedelta(minutes=5))]
        hi, lo, cl = float(df5["High"].iloc[j]), float(df5["Low"].iloc[j]), float(df5["Close"].iloc[j])
        end = ts + pd.Timedelta(minutes=5)
        n += 1
        if (lo <= stop) if ce else (hi >= stop):
            return [(stop, end)]
        if not t1_done and ((hi >= t1) if ce else (lo <= t1)):
            t1_done = True
            stop = max(stop, t1) if ce else min(stop, t1)
        if t1_done:
            st = st15_at(ts)
            if st == st:
                stop = max(stop, st) if ce else min(stop, st)
        if tg is not None and ((hi >= tg) if ce else (lo <= tg)):
            return [(tg, end)]
        if not t1_done and not be_done and n >= WAIT_BARS_5M:
            be_done = True
            if (cl <= entry) if ce else (cl >= entry):
                return [(cl, end)]
            stop = max(stop, entry) if ce else min(stop, entry)
    return [(float(df5["Close"].iloc[-1]), df5.index[-1] + pd.Timedelta(minutes=5))]


def price(key, when, entry_spot, side, legs, sigma, days):
    """pro_study.price()'s money, in times instead of 15-minute bar numbers."""
    import pro_study as ps
    import regime_study as rs
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    exp = ps.expiry_on_or_after(key, when.date(), days)
    call = side == "CE"
    K = round(entry_spot / step) * step
    p0 = rs.bs(entry_spot, K, ps.years_to(exp, when), sigma, call)
    if p0 <= 0.5:
        return None
    buy = p0 * (1 + ps.SLIP) * qty
    sells, orders = 0.0, 1
    for spot, t_exit in legs:
        p1 = rs.bs(spot, K, ps.years_to(exp, t_exit), sigma, call)
        sells += max(p1 * (1 - ps.SLIP), 0.0) * qty
        orders += 1
    brokerage = rs.BROKERAGE_PER_ORDER * orders
    stt = rs.STT_SELL * sells
    txn = rs.TXN[exch] * (buy + sells)
    sebi = rs.SEBI_PER_RUPEE * (buy + sells)
    stamp = rs.STAMP_BUY * buy
    gst = rs.GST * (brokerage + txn + sebi)
    return sells - buy - (brokerage + stt + txn + sebi + stamp + gst)


def run_variant(key, sigs, variant, df5, df15, st15, sigma, days, target):
    def st15_at(ts):
        p = df15.index.searchsorted(ts, side="right") - 2      # the last 15-minute candle CLOSED by ts
        return float(st15[p]) if p >= 0 else float("nan")
    rows, free_at, last = [], None, {"CE": None, "PE": None}
    for now, i, k, r in select(sigs, variant):
        if free_at is not None and now < free_at:
            continue
        side = r[0]
        if last[side] is not None and now < last[side] + COOLDOWN:
            continue
        s = sigma[i]
        if not (s == s) or s <= 0:
            continue
        legs = simulate(df5, st15_at, now, r, target)
        net = price(key, now, r[1], side, legs, s, days)
        if net is None:
            continue
        exit_t = legs[-1][1]
        rows.append({"when": now, "net": net, "k": k, "side": side, "entry": r[1], "index": key,
                     "close15": float(df15["Close"].iloc[i])})
        free_at, last[side] = exit_t, exit_t
    return rows


def main():
    import backtest_intraday as bt
    import pro_study as ps
    import regime_study as rs
    import indicators as ind
    print("Reading every 15-minute candle three times as it forms (this takes a while)...", flush=True)
    with ProcessPoolExecutor(max_workers=3) as ex:
        sigs = dict(ex.map(signals_for, [(k,) for k in INDICES]))
    vix = rs.load_vix()
    hist = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    nrv = np.log(rs.daily_close(hist["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    results = {}
    for variant in ("first signal", "candle close", "confirmed"):
        rows = []
        for k in INDICES:
            df15 = hist[k]
            F = rs.features(df15, vix, nrv)
            st_len, st_mult = config.supertrend_params(k)
            st15 = ind.supertrend(df15, st_len, st_mult)[0].to_numpy()
            rows += run_variant(k, sigs[k], variant, load_5m(k), df15, st15, F["sigma"].to_numpy(),
                                set(df15.index.date), target)
        results[variant] = rows

    def split(rows):
        return {"is": ps.stats([r for r in rows if r["when"] < rs.SPLIT]) or {"n": 0, "total": 0, "pf": 0, "dd": 0, "win": 0, "avg": 0},
                "oos": ps.stats([r for r in rows if r["when"] >= rs.SPLIT]) or {"n": 0, "total": 0, "pf": 0, "dd": 0, "win": 0, "avg": 0}}

    print("=" * 150)
    print(" NIFTY + Bank Nifty + Sensex, per lot after costs, real expiries. Same exits for all (live, on 5-minute candles).")
    print("=" * 150)
    print(f" {'entry':16s} {'IN-SAMPLE: trades, won, total, per trade, profit factor, worst drop':72s} | HELD-OUT FINAL YEAR")
    for v, rows in results.items():
        r = split(rows)
        a, b = r["is"], r["oos"]
        print(f" {v:16s} {a['n']:>5} {a['win']:>4.0f}% ₹{a['total']:>+10,.0f} ₹{a['avg']:>+5,.0f}/tr PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
              f"  | {b['n']:>4} {b['win']:>4.0f}% ₹{b['total']:>+10,.0f} ₹{b['avg']:>+5,.0f}/tr PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")
    base = split(results["first signal"])
    print("\n VERDICT - kept only if better than 'first signal' (today's live behaviour) in BOTH periods")
    for v in ("candle close", "confirmed"):
        r = split(results[v])
        di, do = r["is"]["total"] - base["is"]["total"], r["oos"]["total"] - base["oos"]["total"]
        print(f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  {v:14s} in-sample {di:>+10,.0f}  held-out {do:>+10,.0f}"
              f"   worst drop: in-sample {r['is']['dd'] - base['is']['dd']:>+9,.0f}  held-out {r['oos']['dd'] - base['oos']['dd']:>+9,.0f}")
    print("\n 'FIRST SIGNAL' ENTRIES BY WHEN IN THE CANDLE THEY FIRED - and where the 15-minute candle then closed")
    rows = results["first signal"]
    for k, label in ((1, "after 5 min"), (2, "after 10 min"), (3, "at the close")):
        sub = [r for r in rows if r["k"] == k]
        if not sub:
            continue
        r = split(sub)
        move = [((r_["close15"] - r_["entry"]) if r_["side"] == "CE" else (r_["entry"] - r_["close15"])) for r_ in sub]
        print(f"   {label:14s} {len(sub):>5} entries | in-sample ₹{r['is']['total']:>+10,.0f} (₹{r['is']['avg']:>+5,.0f}/tr)"
              f"  held-out ₹{r['oos']['total']:>+10,.0f} (₹{r['oos']['avg']:>+5,.0f}/tr)"
              f" | index from entry to the candle's close: {np.mean(move):+.1f} pts in the trade's favour")
    return results


if __name__ == "__main__":
    main()
