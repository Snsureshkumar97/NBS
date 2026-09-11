#!/usr/bin/env python3
"""
rule_review.py — are the numbers in the rules the right numbers?
================================================================================
Every rule has a number in it - ADX 20, a 20-minute cooldown, exit at T3,
entries until 15:15, reward at least 1x risk. A professional does not ask
"which value made the most money?" - sweep enough values and one always
does, by luck. The question is whether the value in use sits on a stable
plateau, where its neighbours do about as well, in-sample AND on the held-out
year. A value on a plateau is kept. One sitting on a spike, or clearly beaten
on both periods by a neighbour whose own neighbours agree, is changed.

Each rule is moved on its own, a few standard steps either side, with every
other rule as it runs live now: opening-range break, room to run >= 1x stop,
exit at T2, Nifty and Sensex (Bank Nifty is watch-only). Priced as ATM options
on real expiries after Zerodha's costs, per lot (pro_study.py).

    python3 rule_review.py

WHAT IT FOUND (11 Sep 2026)
  ADX 20              plateau 18-25; kept.
  Room >= 1.0x risk   plateau 1.0-1.2; kept.
  Last entry 15:15    15:00 / 15:15 trade places between periods; kept.
  Entry spacing       profit per trade flat from 2 to 8 bars - tighter only adds
                      trades and drawdown, so the 20-minute cooldown is a risk
                      dial, not an edge; kept.
  Exit target         T3 -> T2: better in both periods, a quarter less
                      in-sample drawdown, T1 clearly worse. CHANGED.
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs

INDICES = ["NIFTY", "SENSEX"]


def load():
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nifty = hists["NIFTY"]
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    ctx = {}
    for k, df in hists.items():
        F = rs.features(df, vix, nrv)
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        mins = (df.index.hour * 60 + df.index.minute + 15).to_numpy()   # entry = bar close
        ctx[k] = {"df": df, "F": F, "A": A, "orb": rs.make_gates(F)["opening-range break"],
                  "sig": F["sigma"].to_numpy(), "mins": mins}
    return ctx


def run(ctx, adx=None, min_rr=1.0, last_entry=(15, 15), gap_bars=4, target="t2",
        rows_out=None):
    saved = (config.ADX_TREND_THRESHOLD, dict(config._STRICTNESS["strict"]))
    if adx is not None:
        config.ADX_TREND_THRESHOLD = adx
        config._STRICTNESS["strict"]["adx"] = adx
    cut = last_entry[0] * 60 + last_entry[1]
    rows = []
    try:
        for k, c in ctx.items():
            orb, mins = c["orb"], c["mins"]

            def gate(i, r):
                if not orb(i, r) or mins[i] > cut:
                    return False
                t3, risk = r["index_targets"][2], r.get("risk_points")
                return bool(risk) and t3 is not None and abs(t3 - r["spot"]) >= min_rr * risk

            res = bt.run(k, c["df"], gate=gate, min_gap_bars=gap_bars)
            pos = {t: n for n, t in enumerate(c["df"].index)}
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = c["sig"][i]
                if not (s == s) or s <= 0:
                    continue
                if target != "t3":
                    tr = dict(tr, t3=tr[target])
                p = ps.price(k, c["df"], c["A"], i, tr, ps.simulate(c["A"], i, tr), s)
                if p:
                    rows.append(p)
    finally:
        config.ADX_TREND_THRESHOLD = saved[0]
        config._STRICTNESS["strict"].clear()
        config._STRICTNESS["strict"].update(saved[1])
    d = pd.DataFrame(rows)
    if rows_out is not None:
        rows_out.extend(rows)
    out = {}
    for lab, part in (("is", d[d["when"] < ps.SPLIT]), ("oos", d[d["when"] >= ps.SPLIT])):
        net = part["net"]
        w, l = net[net > 0].sum(), -net[net < 0].sum()
        eq = net.cumsum()
        out[lab] = {"n": len(net), "total": net.sum(), "avg": net.mean(), "pf": w / l if l else float("nan"),
                    "dd": float((eq.cummax() - eq).max()) if len(eq) else 0.0}
    return out


def main():
    print("Loading history...")
    ctx = load()
    grid = [
        ("ADX floor", "adx", [15, 18, 20, 25, 30], 20),
        ("Reward : risk floor (T3 / stop)", "min_rr", [0.8, 1.0, 1.2, 1.5], 1.0),
        ("Last entry (bar close, IST)", "last_entry", [(13, 30), (14, 30), (15, 0), (15, 15)], (15, 15)),
        ("Spacing between entries (15m bars)", "gap_bars", [2, 4, 8], 4),
        ("Target that closes the trade", "target", ["t1", "t2", "t3"], "t2"),
    ]
    print("\nPer lot, Nifty + Sensex, after costs. IS = to 14 Aug 2025, OOS = the held-out year.\n")
    for title, key, values, live in grid:
        print(f"{title}   (live: {live})")
        print(f"   {'value':>10} | {'IS trades':>9} {'IS total':>11} {'IS avg':>7} {'IS PF':>6} |"
              f" {'OOS trades':>10} {'OOS total':>11} {'OOS avg':>8} {'OOS PF':>6} {'OOS DD':>9}")
        for v in values:
            r = run(ctx, **{key: v})
            a, b = r["is"], r["oos"]
            mark = "  <- live" if v == live else ""
            vs = f"{v[0]:02d}:{v[1]:02d}" if isinstance(v, tuple) else str(v)
            print(f"   {vs:>10} | {a['n']:>9} {a['total']:>+11,.0f} {a['avg']:>+7,.0f} {a['pf']:>6.2f} |"
                  f" {b['n']:>10} {b['total']:>+11,.0f} {b['avg']:>+8,.0f} {b['pf']:>6.2f} {b['dd']:>9,.0f}{mark}")
        print()


if __name__ == "__main__":
    main()
