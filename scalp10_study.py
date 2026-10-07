#!/usr/bin/env python3
"""
scalp10_study.py — enter on the tool's signal once it has momentum, take 10 points and get out (3 years, and at big lots)
================================================================================
The user, 7 Oct 2026: "i want to bactest one startegy everything is same i just want to check how will be the results
if a strike fires and when it have a full momentum we will enter the trade and just exit in 10 points how will be the
profit for 3 last three years" ... "with big lots".

EVERYTHING THE SAME: the tool's own entries (the live entry checks, as every study), its stop, out by the close, one
position per index, the cooldown and its waiver. Two set-ups:
  A. the tool's rules on Nifty, Bank Nifty and Sensex (every study's baseline: +437,509 / +125,227 per lot - checked)
  B. live today: the Trend Rider on Nifty and Bank Nifty (config.SYSTEM_DEFAULTS), the rules on Sensex

"FULL MOMENTUM" has no one meaning, so each of these is measured as the entry, the stop staying where the signal put it:
  signal         - at the signal candle's close, as today
  strong candle  - the signal candle closed the trade's way with a body of at least 60% of its range
  breakout       - wait up to two candles for a CLOSE beyond the signal candle's high (a call) / low (a put); no trade if
                   the stop is touched first
  ADX            - ADX(14) at least 25 and higher than a candle earlier, +DI over -DI for a call (mirrored for a put)
  RSI            - RSI(14) at least 60 for a call, at most 40 for a put
  all three      - strong candle, ADX and RSI together

"EXIT IN 10 POINTS" - three exits on each entry:
  today          - the live exit (rules: T1 steps the stop up, the Supertrend trail, out at T2; Trend Rider: 2.75 x risk)
  +10 premium    - out the moment the option's premium is 10 above what was paid (Rs 650 a Nifty lot, before costs)
  +10 index      - out the moment the index moves 10 points the trade's way
The stop and the close as today (a stop is filled at its level, as every study here; the Trend Rider is re-priced
in this model, so its figures differ a little from pasted_combos_study.py's). Premiums are the harness's: Black-Scholes on the at-the-money strike of the real
expiry with that day's volatility, 0.25% slippage a side, every Zerodha charge. A 15-minute candle that touches both the
stop and the target is counted as the STOP (it does not say which came first); how many trades that decided, and the
total if every one had been the target, are printed under each table.

BIG LOTS: the same trades at 10, 25 and 50 lots - the charges recomputed at the size (an order larger than the
exchange's freeze quantity is sliced, each slice a Rs 20 order), and once more with an extra half point of slippage a
side, which a 10-point target feels at size.

In-sample to 15 Aug 2025, then the held-out final year; KEEP only if more profit than today's exit in BOTH periods.

    python3 scalp10_study.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_intraday as bt
import config
import opening_window_study as ows
import pasted_combos_study as pc
import pro_study as ps
import regime_study as rs
import stop_day_study as sds

INDICES = sds.INDICES
POINTS = 10.0
MOMENTUM = ("signal", "strong", "breakout", "adx", "rsi", "all")
MLABEL = {"signal": "at the signal (today)", "strong": "strong signal candle", "breakout": "breakout close",
          "adx": "ADX >= 25 and rising", "rsi": "RSI 60 / 40", "all": "all three (candle+ADX+RSI)"}
EXITS = ("today", "prem", "idx")
ELABEL = {"today": "today's exit", "prem": "+10 premium", "idx": "+10 index"}
LOTS = (1, 10, 25, 50)
EXTRA_SLIP = 0.5                       # premium points a side, at size
# The exchanges' freeze quantities (contracts per order) - approximate, they are revised; a larger order is sliced.
FREEZE = {"NIFTY": 1800, "BANKNIFTY": 900, "SENSEX": 1000, "MIDCPNIFTY": 2800}
STRONG_BODY = 0.6
ADX_MIN = 25.0
RSI_UP, RSI_DN = 60.0, 40.0
BREAKOUT_BARS = 2
BAR = pd.Timedelta(minutes=15)


# ------------------------------------------------------------------ prices
def solve_spot(goal, K, T, sigma, call, lo, hi):
    """The index level at which the option is worth `goal` (Black-Scholes is monotone in the spot), or None."""
    f = lambda S: rs.bs(S, K, T, sigma, call) - goal
    fa, fb = f(lo), f(hi)
    if fa == 0:
        return lo
    if fa * fb > 0:
        return None
    a, b = lo, hi
    for _ in range(60):
        m = 0.5 * (a + b)
        fm = f(m)
        if (fm > 0) == (fa > 0):
            a, fa = m, fm
        else:
            b = m
    return 0.5 * (a + b)


def contract(k, df, A, ie, entry, side, sigma):
    """(strike, expiry, premium paid) for an entry at bar ie's close - as pro_study.price() picks them."""
    meta = config.INSTRUMENTS[k]
    when = df.index[ie] + BAR
    exp = ps.expiry_on_or_after(k, when.date(), A["days"])
    K = round(entry / meta["strike_step"]) * meta["strike_step"]
    return K, exp, rs.bs(entry, K, ps.years_to(exp, when), sigma, side == "CE")


def quote(k, df, A, ie, entry, side, legs, sigma):
    """The premium paid and the premium sold, per unit, mid (slippage and charges come in net())."""
    K, exp, p0 = contract(k, df, A, ie, entry, side, sigma)
    if p0 <= 0.5:
        return None
    spot, j, _ = legs[-1]
    p1 = rs.bs(spot, K, ps.years_to(exp, df.index[j] + BAR), sigma, side == "CE")
    meta = config.INSTRUMENTS[k]
    return {"p0": p0, "p1": p1, "qty": meta["lot_size"], "exch": "BSE" if meta["kite_exchange"] == "BSE" else "NSE",
            "index": k, "when": df.index[ie] + BAR, "exit_time": df.index[j] + BAR}


def net(q, lots=1, extra=0.0):
    """Rupees for one round trip at `lots`, after every charge pro_study.price() takes (an order over the freeze
    quantity sliced, each slice a Rs 20 order); `extra` premium points of slippage a side on top of the 0.25%."""
    qty = q["qty"] * lots
    buy = (q["p0"] * (1 + ps.SLIP) + extra) * qty
    sell = max(q["p1"] * (1 - ps.SLIP) - extra, 0.0) * qty
    slices = max(1, math.ceil(qty / FREEZE.get(q["index"], 10 ** 9)))
    brokerage = rs.BROKERAGE_PER_ORDER * 2 * slices
    stt = rs.STT_SELL * sell
    txn = rs.TXN[q["exch"]] * (buy + sell)
    sebi = rs.SEBI_PER_RUPEE * (buy + sell)
    stamp = rs.STAMP_BUY * buy
    gst = rs.GST * (brokerage + txn + sebi)
    return sell - buy - (brokerage + stt + txn + sebi + stamp + gst)


# ------------------------------------------------------------------ exits
def walk(A, ie, side, stop, target_at, cutoff_bar=None):
    """Stop / target / close from the bar after ie. target_at(j) -> the index level that is the target at bar j (None:
    no target). Returns (conservative legs, via), (optimistic legs, via), ambiguous: a candle that reaches both the stop
    and the target counts as the stop, and as the target in the optimistic reading."""
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce, n = side == "CE", len(cl)
    for j in range(ie + 1, n):
        tgt = target_at(j)
        stop_hit = (lo[j] <= stop) if ce else (hi[j] >= stop)
        tgt_hit = tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt))
        if stop_hit:
            cons = ([(stop, j, 1.0)], "stop")
            return cons, (([(tgt, j, 1.0)], "target") if tgt_hit else cons), bool(tgt_hit)
        if tgt_hit:
            r = ([(tgt, j, 1.0)], "target")
            return r, r, False
        if end[j] or (cutoff_bar is not None and cutoff_bar[j]):
            r = ([(cl[j], j, 1.0)], "close")
            return r, r, False
    r = ([(cl[n - 1], n - 1, 1.0)], "close")
    return r, r, False


def premium_target(k, df, A, ie, entry, side, sigma, points=POINTS):
    """target_at(j): the index level where the option is worth what was paid + `points`, at bar j's close (time decay
    included) - or None when the option is too cheap to price."""
    K, exp, p0 = contract(k, df, A, ie, entry, side, sigma)
    if p0 <= 0.5:
        return None
    goal, call = p0 + points, side == "CE"
    lo, hi = entry * 0.85, entry * 1.15
    return lambda j: solve_spot(goal, K, ps.years_to(exp, df.index[j] + BAR), sigma, call, lo, hi)


# ------------------------------------------------------------------ momentum
def momentum(m, i, side, stop, A, P):
    """(entry bar, entry price) under momentum rule m for a signal at bar i, or None."""
    ce = side == "CE"
    cl, op, hi, lo, end = A["cl"], P["open"], A["hi"], A["lo"], A["end"]
    rng = hi[i] - lo[i]
    strong = rng > 0 and (cl[i] > op[i] if ce else cl[i] < op[i]) and abs(cl[i] - op[i]) >= STRONG_BODY * rng
    a, a1 = P["adx"][i], P["adx"][i - 1] if i > 0 else np.nan
    di = (P["pdi"][i] > P["mdi"][i]) if ce else (P["mdi"][i] > P["pdi"][i])
    adx_ok = a == a and a1 == a1 and a >= ADX_MIN and a > a1 and di
    r = P["rsi"][i]
    rsi_ok = r == r and (r >= RSI_UP if ce else r <= RSI_DN)
    if m == "signal":
        return i, cl[i]
    if m == "strong":
        return (i, cl[i]) if strong else None
    if m == "adx":
        return (i, cl[i]) if adx_ok else None
    if m == "rsi":
        return (i, cl[i]) if rsi_ok else None
    if m == "all":
        return (i, cl[i]) if strong and adx_ok and rsi_ok else None
    if m == "breakout":
        if end[i]:
            return None
        for j in range(i + 1, min(i + 1 + BREAKOUT_BARS, len(cl))):
            if (lo[j] <= stop) if ce else (hi[j] >= stop):
                return None
            if (cl[j] > hi[i]) if ce else (cl[j] < lo[i]):
                return None if end[j] else (j, cl[j])
            if end[j]:
                return None
        return None
    raise ValueError(m)


# ------------------------------------------------------------------ one index
def _arrays(df, pre):
    return {"open": df["Open"].to_numpy(), "adx": pre["adx"].to_numpy(), "pdi": pre["plus_di"].to_numpy(),
            "mdi": pre["minus_di"].to_numpy(), "rsi": pre["rsi"].to_numpy()}


def _row(k, df, A, ie, entry, side, legs_via, sigma, adx, amb, opt_legs_via):
    legs, via = legs_via
    q = quote(k, df, A, ie, entry, side, legs, sigma)
    if q is None:
        return None
    qo = quote(k, df, A, ie, entry, side, opt_legs_via[0], sigma)
    exit_bar = legs[-1][1]
    return dict(q, side=side, closed_via=via, exit_adx=float(adx[exit_bar]), net=net(q), amb=amb,
                net_opt=net(qo) if qo else net(q))


def rules_rows(k):
    """{(momentum, exit): raw rows} for the tool's rules on index k (before the one-position walk)."""
    c = sds._context()
    df, A, st, pre = c["hists"][k], c["A"][k], c["st"][k], c["pre"][k]
    P, sig, adx = _arrays(df, pre), c["F"][k]["sigma"].to_numpy(), pre["adx"].to_numpy()
    trades = bt.run(k, df, gate=c["live"][k])["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    out = {(m, e): [] for m in MOMENTUM for e in EXITS}
    for tr in trades:
        i = pos[tr["when"]]
        s = sig[i]
        if not (s == s) or s <= 0:
            continue
        for m in MOMENTUM:
            got = momentum(m, i, tr["side"], tr["stop"], A, P)
            if got is None:
                continue
            ie, entry = got
            if ie == i:
                entry = tr["entry"]                  # the signal candle: exactly the entry every study prices
            si = sig[ie] if sig[ie] == sig[ie] and sig[ie] > 0 else s
            t = dict(tr, entry=entry)
            for e in EXITS:
                if e == "today":
                    legs = ows.simulate_live(A, ie, t, st, c["target"])
                    px = legs[-1][0]
                    via = ("stop" if abs(px - t["stop"]) < 1e-6 else
                           "target" if abs(px - t[c["target"]]) < 1e-6 else "other")
                    cons = opt = (legs, via)
                    amb = False
                elif e == "prem":
                    fn = premium_target(k, df, A, ie, entry, tr["side"], si)
                    if fn is None:
                        continue
                    cons, opt, amb = walk(A, ie, tr["side"], tr["stop"], fn)
                else:
                    lvl = entry + POINTS if tr["side"] == "CE" else entry - POINTS
                    cons, opt, amb = walk(A, ie, tr["side"], tr["stop"], lambda j, lvl=lvl: lvl)
                r = _row(k, df, A, ie, entry, tr["side"], cons, si, adx, amb, opt)
                if r is not None:
                    out[(m, e)].append(r)
    return out


def trend_rider_rows(k):
    """{(momentum, exit): rows} for the Trend Rider on index k, walked one position at a time as it runs live (its first
    close where every condition holds; stop on the last 5 candles; 2.75 x risk; out by 15:15; no cooldown)."""
    c = sds._context()
    df, A, pre = c["hists"][k], c["A"][k], c["pre"][k]
    P, sig, adx = _arrays(df, pre), c["F"][k]["sigma"].to_numpy(), pre["adx"].to_numpy()
    tp = config.TREND_RIDER
    long_, short = pc.signals(df[["Open", "High", "Low", "Close", "Volume"]], 2, tp["adx_min"])
    tod = np.array([t.time() for t in df.index])
    date = np.array([t.date() for t in df.index])
    last = (pd.Timestamp("2000-01-01 15:15") - BAR).time()          # the candle that ends 15:15
    cutoff = tod >= last
    lo_, hi_ = A["lo"], A["hi"]
    n = len(df)
    out = {(m, e): [] for m in MOMENTUM for e in EXITS}
    for m in MOMENTUM:
        for e in EXITS:
            rows, free_from = out[(m, e)], 0
            for i in range(60, n - 1):
                if i < free_from:
                    continue
                side = "CE" if long_[i] and not long_[i - 1] else "PE" if short[i] and not short[i - 1] else None
                if side is None or tod[i] >= last or date[i + 1] != date[i]:
                    continue
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                w = tp["swing"]
                stop = lo_[max(0, i - w + 1):i + 1].min() if side == "CE" else hi_[max(0, i - w + 1):i + 1].max()
                got = momentum(m, i, side, stop, A, P)
                if got is None:
                    continue
                ie, entry = got
                if cutoff[ie]:
                    continue
                risk = entry - stop if side == "CE" else stop - entry
                if risk <= 0:
                    continue
                if e == "today":
                    lvl = entry + tp["target_r"] * risk if side == "CE" else entry - tp["target_r"] * risk
                    fn = lambda j, lvl=lvl: lvl
                elif e == "prem":
                    fn = premium_target(k, df, A, ie, entry, side, s)
                    if fn is None:
                        continue
                else:
                    lvl = entry + POINTS if side == "CE" else entry - POINTS
                    fn = lambda j, lvl=lvl: lvl
                cons, opt, amb = walk(A, ie, side, stop, fn, cutoff_bar=cutoff)
                r = _row(k, df, A, ie, entry, side, cons, s, adx, amb, opt)
                if r is None:
                    continue
                rows.append(r)
                free_from = cons[0][-1][1] + 1
    return out


def _job(k):
    return k, rules_rows(k), trend_rider_rows(k)


# ------------------------------------------------------------------ report
def scaled(rows, lots, extra=0.0):
    return [dict(r, net=net(r, lots, extra)) for r in rows]


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Replaying the tool's entries and the Trend Rider's on all three indices, every momentum rule and exit...",
          flush=True)
    with ProcessPoolExecutor(max_workers=len(INDICES)) as ex:
        got = {k: (ru, trr) for k, ru, trr in ex.map(_job, INDICES)}

    A = {key: sds.per_index([r for k in INDICES for r in got[k][0][key]]) for key in got[INDICES[0]][0]}
    base = A[("signal", "today")]
    b = sds.split(base)
    assert (round(b["is"]["total"]), round(b["oos"]["total"])) == (437509, 125227), \
        f"set-up A at the signal with today's exit must be every study's baseline: {b['is']['total']:.0f} / {b['oos']['total']:.0f}"
    # B: live today - the Trend Rider on Nifty and Bank Nifty, the rules on Sensex
    B = {}
    for key in A:
        rows = [r for r in A[key] if config.SYSTEM_DEFAULTS.get(r["index"], "rules") == "rules"]
        rows += [r for k in INDICES if config.SYSTEM_DEFAULTS.get(k) == "trend_rider" for r in got[k][1][key]]
        B[key] = sorted(rows, key=lambda r: r["when"])

    W = 172
    for name, res in (("A. THE TOOL'S RULES on Nifty, Bank Nifty and Sensex", A),
                      ("B. LIVE TODAY: the Trend Rider on Nifty and Bank Nifty, the rules on Sensex", B)):
        print("=" * W)
        print(f" {name} - per lot, after costs, real expiries.  columns: trades, won, total, profit factor, worst drop, "
              f"worst single DAY")
        print("=" * W)
        print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
        for m in MOMENTUM:
            print("-" * W)
            print(f" ENTRY: {MLABEL[m]}")
            for e in EXITS:
                print(sds.line(f"   exit: {ELABEL[e]}", res[(m, e)]))
        print("-" * W)
        print("\n VERDICT vs today (at the signal, today's exit) - more profit in BOTH periods?")
        for m in MOMENTUM:
            for e in EXITS:
                if (m, e) != ("signal", "today"):
                    print(sds.verdict(f"{MLABEL[m]}, {ELABEL[e]}", res[(m, e)], res[("signal", "today")]))
        print("\n A CANDLE THAT TOUCHED BOTH THE STOP AND THE +10 (counted as the stop above) - and the 3-year total if "
              "every one had been the target instead:")
        for m in MOMENTUM:
            for e in ("prem", "idx"):
                rows = res[(m, e)]
                amb = sum(1 for r in rows if r["amb"])
                print(f"   {MLABEL[m]:30s} {ELABEL[e]:13s} {amb:>4} of {len(rows):>5} trades   3-year total "
                      f"₹{sum(r['net'] for r in rows):>+11,.0f} as counted, ₹{sum(r['net_opt'] for r in rows):>+11,.0f} "
                      f"if all were the target")
        print("\n BIG LOTS - the 3-year total (in-sample + held-out) and the held-out year alone, at each size; charges "
              "recomputed at the size, an order over the freeze quantity sliced")
        print(f"   {'':52s}" + "".join(f"{str(n) + ' lot' + ('s' if n > 1 else ''):>26s}" for n in LOTS))
        for m in MOMENTUM:
            for e in EXITS:
                rows = res[(m, e)]
                for extra in (0.0, EXTRA_SLIP):
                    if e == "today" and extra:
                        continue
                    cells = []
                    for n_ in LOTS:
                        s_ = sds.split(scaled(rows, n_, extra if n_ > 1 else 0.0))
                        cells.append(f"₹{s_['is']['total'] + s_['oos']['total']:>+12,.0f} / {s_['oos']['total']:>+10,.0f}")
                    tag = f"{MLABEL[m]}, {ELABEL[e]}" + (f" +{EXTRA_SLIP} pt slip a side at size" if extra else "")
                    print(f"   {tag:52s}" + "".join(f"{c:>26s}" for c in cells))
        print()


if __name__ == "__main__":
    main()
