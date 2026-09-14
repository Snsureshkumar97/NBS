#!/usr/bin/env python3
"""
btst_study.py - does Buy Today, Sell Tomorrow make money on index options, after costs?
================================================================================
Each pre-declared rule in btst.py is replayed over three years of 15-minute
Nifty, Bank Nifty and Sensex candles. A signal buys the ATM option at the
15:30 close and sells it at the next session's 09:30 close.

HOW EACH TRADE IS PRICED (regime_study's model, so the numbers are comparable)
  * Black-Scholes at the entry and at the exit, with yesterday's India VIX
    scaled by the index's realised volatility against Nifty's.
  * Time decay for the REAL calendar gap: one night is 18 hours, a weekend
    is three days, a holiday more. A contract that would expire inside the
    hold is rolled to the next weekly.
  * Zerodha's charges on both legs (brokerage, STT, exchange, SEBI, stamp,
    GST) and slippage of regime_study.SLIP a side.

WHAT GUARDS AGAINST FOOLING OURSELVES
  * The rules were fixed in btst.py before this was run.
  * The last year (from regime_study.SPLIT) is held out. The verdict, also
    declared in advance: a rule's side WORKS on an index only if it is net
    positive after costs in BOTH periods and the held-out year has at least
    30 trades. ROBUST additionally needs the held-out year still positive at
    2 and 5 days to expiry, with a 09:15 exit, and at double slippage.
  * Six rules x two sides x three indices is a lot of chances for one to look
    good by luck. The report counts the comparisons; read a lone pass with
    that in mind.

WHAT THIS STILL ASSUMES
  * Volatility is the same at the exit as at the entry. Implied volatility
    often eases overnight and after events, which works against the buyer and
    is not modelled - real results would be somewhat worse than these.
  * Days to expiry is an assumption per index (swept), not the true calendar.
  * Entry exactly at the closing price; in practice you buy a few minutes
    before 15:30 at whatever the offer is.

    python3 btst_study.py
"""
import datetime as dt
import json
import math
import os
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import btst
import config
import regime_study as rs
import trade_log

INDICES = rs.INDICES
MIN_OOS_TRADES = 30
OUT_NAME = "btst_study.json"


def price(key, d, row, side, sigma, dte, slip, exit_col):
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
    s0, s1, nd = row["close"], row[exit_col], row["next_date"]
    if nd is None or pd.isna(s1) or sigma is None or pd.isna(sigma) or sigma <= 0:
        return None
    exit_t = dt.time(9, 30) if exit_col == "next_c0930" else dt.time(9, 15)
    gap = (dt.datetime.combine(nd, exit_t)
           - dt.datetime.combine(d, dt.time(15, 30))).total_seconds() / 86400.0
    dte_eff = dte if dte - gap >= 0.5 else dte + 7.0       # would expire overnight: next weekly
    call = side == "CE"
    k = round(s0 / step) * step
    p0 = rs.bs(s0, k, dte_eff / 365.0, sigma, call)
    p1 = rs.bs(s1, k, max(dte_eff - gap, 1e-6) / 365.0, sigma, call)
    if p0 <= 0.5:
        return None
    return {"when": row["entry_ts"], "date": str(d), "side": side,
            "net": rs.net_rupees(p0, p1, qty, exch, slip), "gross": (p1 - p0) * qty,
            "move": (s1 - s0) if call else (s0 - s1), "gap_days": gap, "p0": p0}


def replay(key, table, sigma_day, dte, slip, exit_col):
    """{rule: {side: [priced trades]}}"""
    out = {r: {"CE": [], "PE": []} for r in btst.RULES}
    for d, row in table.iterrows():
        for rule in btst.RULES:
            side = btst.signal(rule, row)
            if side is None:
                continue
            t = price(key, d, row, side, sigma_day.get(d), dte, slip, exit_col)
            if t:
                out[rule][side].append(t)
    return out


def split_stats(trades):
    ins = [t for t in trades if t["when"] < rs.SPLIT]
    oos = [t for t in trades if t["when"] >= rs.SPLIT]
    def st(rows):
        s = rs.stats(rows)
        if s is None:
            return None
        if s["pf"] == float("inf"):
            s["pf"] = None
        s["avg_move_pts"] = float(np.mean([r["move"] for r in rows]))
        s["weekend_share"] = float(np.mean([r["gap_days"] > 1.5 for r in rows]) * 100)
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()}
    return st(ins), st(oos)


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrets = np.log(rs.daily_close(hists["NIFTY"])).diff()
    nifty_rv = nrets.rolling(20).std() * math.sqrt(252)

    scenarios = {"main": (None, rs.SLIP, "next_c0930"),
                 "dte_2": (2.0, rs.SLIP, "next_c0930"),
                 "dte_5": (5.0, rs.SLIP, "next_c0930"),
                 "exit_0915": (None, rs.SLIP, "next_o0915"),
                 "double_slip": (None, rs.SLIP * 2, "next_c0930")}

    results, first, last = {}, None, None
    for key in INDICES:
        f = rs.features(hists[key], vix, nifty_rv)
        sigma_day = f["sigma"].groupby(f.index.date).last()
        table = btst.day_table(hists[key])
        first = min(first or table.index[0], table.index[0])
        last = max(last or table.index[-1], table.index[-1])
        runs = {name: replay(key, table, sigma_day, dte if dte else rs.DTE_DAYS[key], slip, ex)
                for name, (dte, slip, ex) in scenarios.items()}
        results[key] = {}
        for rule in btst.RULES:
            results[key][rule] = {}
            for side in ("CE", "PE"):
                if rule == "always_ce" and side == "PE" or rule == "always_pe" and side == "CE":
                    continue
                ins, oos = split_stats(runs["main"][rule][side])
                works = bool(ins and oos and ins["total"] > 0 and oos["total"] > 0
                             and oos["n"] >= MIN_OOS_TRADES)
                sens = {}
                for name in scenarios:
                    if name == "main":
                        continue
                    _, o = split_stats(runs[name][rule][side])
                    sens[name] = o["total"] if o else None
                robust = works and all(v is not None and v > 0 for v in sens.values())
                results[key][rule][side] = {"is": ins, "oos": oos, "works": works,
                                            "robust": robust, "sensitivity_oos_total": sens}

    comparisons = sum(len(v) for k in results for v in results[k].values())
    report(results, comparisons)
    payload = {
        "generated": dt.datetime.now().isoformat(timespec="minutes"),
        "data_from": str(first), "data_to": str(last), "split": str(rs.SPLIT.date()),
        "entry": "15:30 close", "exit": "next session's 09:30 close",
        "dte_days": rs.DTE_DAYS, "slippage_per_side": rs.SLIP,
        "min_oos_trades": MIN_OOS_TRADES, "comparisons": comparisons,
        "rules": btst.RULES, "results": results,
    }
    path = os.path.join(trade_log.log_dir(), OUT_NAME)
    with open(path + ".tmp", "w") as fh:
        json.dump(payload, fh, indent=1, default=str)
    os.replace(path + ".tmp", path)
    print(f"\nSaved for the BTST section -> {path}")


def fmt(s):
    if not s:
        return f"{'no trades':>42s}"
    return (f"{s['n']:4d} tr  win {s['win']:4.0f}%  avg ₹{s['avg']:>+7,.0f}  "
            f"total ₹{s['total']:>+9,.0f}")


def report(results, comparisons):
    print(f"\nBTST: buy the ATM option at the 15:30 close, sell at the next 09:30 close.")
    print(f"Days to expiry {rs.DTE_DAYS}, slippage {rs.SLIP*100:.2f}% a side, Zerodha charges.")
    print(f"In-sample to {rs.SPLIT.date()}, held out from then. Money is per ONE lot.\n")
    passes = []
    for key in INDICES:
        print("=" * 118)
        print(f" {key}")
        print(f" {'rule':18s} side  {'IN-SAMPLE':42s}  {'HELD-OUT YEAR':42s}  verdict")
        for rule, sides in results[key].items():
            for side, r in sides.items():
                v = "ROBUST" if r["robust"] else "works" if r["works"] else "-"
                if r["works"]:
                    passes.append((key, rule, side, v))
                print(f" {btst.RULES[rule]['label']:18s} {side:4s}  {fmt(r['is'])}  {fmt(r['oos'])}  {v}")
        print()
    print("=" * 118)
    print(f" {comparisons} rule/side/index combinations tested.")
    if passes:
        print(f" Passed both periods: {len(passes)}")
        for p in passes:
            print(f"   {p[0]:10s} {btst.RULES[p[1]]['label']:18s} {p[2]}  {p[3]}")
    else:
        print(" None passed both periods after costs.")
    print(" With this many comparisons, one or two passes are expected by chance alone.")


if __name__ == "__main__":
    main()
