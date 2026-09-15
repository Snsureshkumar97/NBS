#!/usr/bin/env python3
"""
skills_study.py — three ideas from the NSE trading skills, tested before any is trusted
=======================================================================================
PRE-DECLARED 15 Sep 2026, before this was run. From the skills installed that day
(Bhala-Srinivash/nse-trading-skills). Each is layered on the rules live today
(wait for the 09:15-09:45 range, no break, T3 >= 1x the stop, all three indices,
exit at T2), priced as pro_study.py prices them: real expiries, per lot, Zerodha's
charges, 0.25% slippage. A variant is kept only if it beats the live rules in BOTH
the first two years and the held-out year. One setting each, not swept, not
re-tuned after the result.

  B. ATR trailing stop (trailing-stops): once T1 is touched, the stop trails the
     best price since entry by 1x ATR(14) at entry, and never loosens. Exit still
     at T2, the stop, the time limit or the square-off.
  C. Higher timeframe agrees (multi-timeframe-analysis): a CE only when the last
     completed 1-hour close is above its 20-period EMA; a PE only when below.
  D. No entry into an RSI divergence (rsi-divergence): skip a CE when the entry
     close is a new 20-bar closing high but RSI(14) is more than 2 points under
     its value at the previous high (5 to 20 bars back); the mirror for a PE.
  A bar without a reading yet is not held against the trade.

    python3 skills_study.py
"""
import json
import math
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
TARGET = str(getattr(config, "EXIT_AT_TARGET", "T2")).lower()


def rr_ok(r):
    return bool(r.get("risk_points") and r["index_targets"][2] is not None
                and abs(r["index_targets"][2] - r["spot"]) >= r["risk_points"])


def trail_sim(A, i, tr, trail, hold_bars=26):
    """ps.simulate's order (stop, T1, target, square-off), plus a trail after T1."""
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, t1_done, best = tr["stop"], False, tr["entry"]
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            return [(stop, j, 1.0)]
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
        tgt = tr.get(TARGET)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            return [(tgt, j, 1.0)]
        if end[j]:
            return [(cl[j], j, 1.0)]
        best = max(best, hi[j]) if ce else min(best, lo[j])
        if t1_done:
            stop = max(stop, best - trail) if ce else min(stop, best + trail)
    last = min(i + hold_bars, n - 1)
    return [(cl[last], last, 1.0)]


def hourly_view(df):
    """Last completed 1-hour close and its EMA(20), as known at each 15-minute close."""
    t_close = df.index + pd.Timedelta(minutes=15)
    s = pd.Series(df["Close"].to_numpy(), index=t_close)
    grp = s.resample("60min", label="right", closed="right", offset="15min")
    h = pd.DataFrame({"close": grp.last(), "known": s.index.to_series().resample(
        "60min", label="right", closed="right", offset="15min").max()}).dropna()
    h["ema"] = h["close"].ewm(span=20, adjust=False).mean()
    h = h.sort_values("known")
    left = pd.DataFrame({"t": t_close}).reset_index(drop=True)
    m = pd.merge_asof(left, h.reset_index(drop=True), left_on="t", right_on="known", direction="backward")
    return m["close"].to_numpy(), m["ema"].to_numpy()


def divergence(c, rsi, i, side):
    if i < 21 or not (rsi[i] == rsi[i]):
        return False
    lo_, hi_ = i - 20, i - 4
    if side == "CE":
        j = lo_ + int(np.argmax(c[lo_:hi_]))
        return c[i] >= c[i - 20:i].max() and c[i] > c[j] and rsi[j] == rsi[j] and rsi[i] < rsi[j] - 2
    j = lo_ + int(np.argmin(c[lo_:hi_]))
    return c[i] <= c[i - 20:i].min() and c[i] < c[j] and rsi[j] == rsi[j] and rsi[i] > rsi[j] + 2


def main():
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    rows = {"live": [], "B trail": [], "C hourly agrees": [], "D no divergence": []}
    checks = {"trail off reproduces live exits": True}
    for k in INDICES:
        df = hists[k]
        F = rs.features(df, vix, nrv)
        ready = F["or_ready"].to_numpy()
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        atr = ind.atr(df, config.ATR_LENGTH).to_numpy()
        rsi = ind.rsi(df["Close"], 14).to_numpy()
        c = df["Close"].to_numpy()
        hc, he = hourly_view(df)
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F["sigma"].to_numpy()

        def price_all(res, sim):
            out = []
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                p = ps.price(k, df, A, i, tr, sim(i, tr), s)
                if p:
                    out.append(p)
            return out

        live = bt.run(k, df, gate=lambda i, r: bool(ready[i]) and rr_ok(r))
        for tr in live["trades"][:400]:
            i = pos[tr["when"]]
            if trail_sim(A, i, tr, float("inf")) != ps.simulate(A, i, tr):
                checks["trail off reproduces live exits"] = False
        rows["live"] += price_all(live, lambda i, tr: ps.simulate(A, i, tr))
        rows["B trail"] += price_all(live, lambda i, tr: trail_sim(A, i, tr, atr[i] if atr[i] == atr[i] else float("inf")))

        def c_gate(i, r):
            if not (bool(ready[i]) and rr_ok(r)):
                return False
            if not (hc[i] == hc[i] and he[i] == he[i]):
                return True
            return hc[i] > he[i] if ps._side(r) == "CE" else hc[i] < he[i]
        rows["C hourly agrees"] += price_all(bt.run(k, df, gate=c_gate), lambda i, tr: ps.simulate(A, i, tr))

        def d_gate(i, r):
            return bool(ready[i]) and rr_ok(r) and not divergence(c, rsi, i, ps._side(r))
        rows["D no divergence"] += price_all(bt.run(k, df, gate=d_gate), lambda i, tr: ps.simulate(A, i, tr))
        print(f"[{k}] done", flush=True)

    def st(xs):
        s = ps.stats(xs)
        return None if not s else {"n": s["n"], "total": round(s["total"]), "avg": round(s["avg"]),
                                   "pf": round(s["pf"], 2), "dd": round(s["dd"]), "win": round(s["win"], 1)}
    out = {"checks": checks, "results": {}}
    for name, rs_ in rows.items():
        out["results"][name] = {
            per: {"all": st([r for r in rs_ if f(r)]),
                  "Nifty+Sensex": st([r for r in rs_ if f(r) and r["index"] != "BANKNIFTY"])}
            for per, f in (("in_sample", lambda r: r["when"] < rs.SPLIT), ("held_out", lambda r: r["when"] >= rs.SPLIT))}
    base = out["results"]["live"]
    out["verdict"] = {}
    for name in ("B trail", "C hourly agrees", "D no divergence"):
        r = out["results"][name]
        di = r["in_sample"]["all"]["total"] - base["in_sample"]["all"]["total"]
        do = r["held_out"]["all"]["total"] - base["held_out"]["all"]["total"]
        out["verdict"][name] = {"in_sample_vs_live": di, "held_out_vs_live": do, "keep": di > 0 and do > 0}
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
