#!/usr/bin/env python3
"""
gann_volume_study.py — Gann Square of Nine and a volume oscillator, tested before trusted
================================================================================
Asked for by the user on 20 Sep 2026: "gann square of nine calculator with a
volume oscillator see if it improves the tool". Same discipline as pro_study.py:
each idea is ONE pre-declared variant layered on the rules the tool runs now,
kept only if it beats the live rules in-sample AND on the held-out final year,
after Zerodha's costs, priced as the option you would buy. Nothing here changes
the tool; it reports.

GANN SQUARE OF NINE
    The spiral of integers whose diagonals and cardinals fall on the squares of
    r + 0.25, r + 0.5 ... where r = sqrt(price): one full turn of the square adds
    2 to the square root, a 45-degree step adds 0.25. Traders read those steps
    as support and resistance. Levels used here: (sqrt(close) + k/4)^2 for every
    integer k - the 45-degree grid - measured from the entry bar's close.

    Variant G1 "Gann room": skip the entry when the next Gann level in the
        trade's direction is nearer than the stop distance - a level in the
        way before the trade has earned its own risk.
    Variant G2 "Gann level behind": take the entry only when the nearest Gann
        level against the trade (below for a CE, above for a PE) is within half
        an ATR(14) - an entry off a level rather than into thin air.

VOLUME OSCILLATOR
    (EMA5 of volume - EMA20 of volume) / EMA20 of volume, in percent, at the
    entry bar. Above zero, participation is rising.
    Variant V1 "volume rising": skip the entry when the oscillator is at or
        below zero.

    An index prints no volume, and the 15-minute history cache carries zeros
    for NIFTY / BANKNIFTY / SENSEX; the volume that means anything there is the
    near-month future's, which needs Zerodha's continuous futures history (a
    session-day job). So V1 is measured on BITCOIN, whose candles carry real
    volume, and reported for the indices only once that history is fetched.

WHAT IS MEASURED
    Indian indices: pro_study's own machinery - real expiries, option pricing,
    costs, in-sample to 14 Aug 2025 and the held-out year after - rupees per
    lot, pooled across the three. Bitcoin: backtest_intraday's own run, in
    index points per contract, split at the same date.

    python3 gann_volume_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import indicators
import pro_study as ps
import regime_study as rs

STEP = 0.25            # 45 degrees on the square, in square-root units
BEHIND_ATR = 0.5       # G2: a level within this many ATRs behind the entry
VO_FAST, VO_SLOW = 5, 20


def gann_levels(close):
    """(next level below, next level above) the close on the 45-degree grid."""
    r = math.sqrt(max(float(close), 1e-9))
    base = math.floor(r / STEP) * STEP
    below, above = base ** 2, (base + STEP) ** 2
    if below >= close:
        below = (base - STEP) ** 2
    if above <= close:
        above = (base + 2 * STEP) ** 2
    return below, above


def volume_oscillator(volume, fast=VO_FAST, slow=VO_SLOW):
    v = pd.Series(volume, dtype=float)
    ef, es = v.ewm(span=fast, adjust=False).mean(), v.ewm(span=slow, adjust=False).mean()
    return ((ef - es) / es.replace(0, np.nan) * 100.0).to_numpy()


def features(df):
    cl = df["Close"].to_numpy()
    lv = np.array([gann_levels(c) for c in cl])
    return {"below": lv[:, 0], "above": lv[:, 1],
            "atr": indicators.atr(df, 14).to_numpy(),
            "vo": volume_oscillator(df["Volume"].to_numpy()) if (df["Volume"] > 0).any() else None}


def g1_room(X):
    """Skip when the next Gann level in the trade's direction is nearer than the stop."""
    def gate(i, r):
        risk = r.get("risk_points")
        if not risk:
            return True
        spot = float(r["spot"])
        nxt = X["above"][i] if r["option_type"] == "CE" else X["below"][i]
        return abs(nxt - spot) >= risk
    return gate


def g2_behind(X):
    """Only when a Gann level sits within half an ATR behind the entry."""
    def gate(i, r):
        atr = X["atr"][i]
        if not (atr == atr) or atr <= 0:
            return False
        spot = float(r["spot"])
        behind = X["below"][i] if r["option_type"] == "CE" else X["above"][i]
        return abs(spot - behind) <= BEHIND_ATR * atr
    return gate


def v1_rising(X):
    """Skip when the volume oscillator is at or below zero."""
    def gate(i, r):
        vo = X["vo"][i] if X["vo"] is not None else np.nan
        return vo == vo and vo > 0
    return gate


VARIANTS = {"G1 Gann room": g1_room, "G2 Gann level behind": g2_behind, "V1 volume rising": v1_rising}


def study_indices():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in ps.INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in ps.INDICES}
    X = {k: features(hists[k]) for k in ps.INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in ps.INDICES}

    def evaluate(extra):
        rows, blocked = [], 0
        for k in ps.INDICES:
            df = hists[k]
            g = extra(X[k]) if extra else None
            gate = (lambda i, r, k=k, g=g: base_gate[k](i, r) and (g is None or g(i, r)))
            res = bt.run(k, df, gate=gate)
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < ps.SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= ps.SPLIT])}

    def line(name, r):
        a, b = r["is"] or {}, r["oos"] or {}
        f = lambda s: (f"{s['n']:>5} ₹{s['total']:>+10,.0f} PF {s['pf']:4.2f} DD ₹{s['dd']:>8,.0f}" if s else "   no trades")
        return f" {name:28s} {f(a)}  | {f(b)}"

    print("=" * 120)
    print(" INDIAN INDICES - per lot, pooled, real expiries, after costs.  in-sample | held-out final year")
    print("=" * 120)
    base = evaluate(None)
    print(line("LIVE RULES", base))
    out = {}
    for name, fn in VARIANTS.items():
        if name.startswith("V1"):
            if all(X[k]["vo"] is None for k in ps.INDICES):
                print(f" {name:28s} not measured: the index history carries no volume (needs the futures' volume)")
                continue
        out[name] = evaluate(fn)
        print(line("live + " + name, out[name]))
    print("\n VERDICT - kept only if better than LIVE RULES both in-sample and held-out")
    for name, r in out.items():
        if not (r["is"] and r["oos"] and base["is"] and base["oos"]):
            print(f"   drop  {name:28s} too few trades to compare")
            continue
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        print(f"   {'KEEP ' if di > 0 and do > 0 else 'drop '} {name:28s} in-sample {di:>+10,.0f}   held-out {do:>+10,.0f}"
              f"   held-out drawdown {dd:>+9,.0f}")
    return base, out


def study_bitcoin():
    df = bt.fetch_history("BTC", years=3, use_cache=True)
    X = features(df)
    split = ps.SPLIT.tz_convert(df.index.tz) if df.index.tz is not None else ps.SPLIT.tz_localize(None)

    def evaluate(extra):
        g = extra(X) if extra else None
        res = bt.run("BTC", df, gate=g)
        tr = res["trades"]
        def stats(sel):
            if not sel:
                return None
            # Points per contract: the index move from entry to exit, signed by the side.
            p = [(float(t["exit"]) - float(t["entry"])) * (1.0 if t["side"] == "CE" else -1.0) for t in sel]
            wins = [x for x in p if x > 0]
            losses = [x for x in p if x < 0]
            pf = (sum(wins) / -sum(losses)) if losses else float("inf")
            eq = peak = dd = 0.0
            for x in p:
                eq += x
                peak = max(peak, eq)
                dd = max(dd, peak - eq)
            return {"n": len(p), "total": sum(p), "pf": pf, "dd": dd}
        return {"is": stats([t for t in tr if t["when"] < split]), "oos": stats([t for t in tr if t["when"] >= split])}

    def line(name, r):
        f = lambda s: (f"{s['n']:>5} {s['total']:>+10,.0f} pts PF {s['pf']:4.2f} DD {s['dd']:>8,.0f}" if s else "   no trades")
        return f" {name:28s} {f(r['is'])}  | {f(r['oos'])}"

    print("\n" + "=" * 120)
    print(" BITCOIN - index points per contract, no option pricing.  in-sample | held-out (from 15 Aug 2025)")
    print("=" * 120)
    base = evaluate(None)
    print(line("LIVE RULES", base))
    out = {}
    for name, fn in VARIANTS.items():
        out[name] = evaluate(fn)
        print(line("live + " + name, out[name]))
    print("\n VERDICT")
    for name, r in out.items():
        if not (r["is"] and r["oos"] and base["is"] and base["oos"]):
            print(f"   drop  {name:28s} too few trades to compare")
            continue
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        print(f"   {'KEEP ' if di > 0 and do > 0 else 'drop '} {name:28s} in-sample {di:>+10,.0f}   held-out {do:>+10,.0f}")
    return base, out


if __name__ == "__main__":
    study_indices()
    study_bitcoin()
