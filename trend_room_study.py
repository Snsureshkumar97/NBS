#!/usr/bin/env python3
"""
trend_room_study.py — more room to run on a trend day, tested before it is trusted
==================================================================================
PRE-DECLARED 15 Sep 2026, before this was run. Prompted by that day: every index
used its whole normal range by 10:30 and went on to travel 2.5-3x it, while the
room-to-run estimate - what is left of a normal day - said there was none, and
four put signals the live tool held for "low reward" would each have reached T2.

The variant (config.TREND_DAY_ROOM): once a day has used at least its normal
range (after the ADX expansion) and price is within 20% of the day's range from
its extreme, the room in THAT direction is one more normal day's range instead
of what is left of one. Everything else is unchanged - the other direction, the
other limits, the engine's own 0.6 room check, the 1x rule on the ticket.

Measured on the rules live today - wait for the 09:15-09:45 range, no break,
T3 >= 1x the stop, no entry into an RSI divergence, all three indices, exit at
T2 - priced as pro_study.py prices them: real expiries, per lot, Zerodha's
charges, 0.25% slippage, drawdowns walked in time order. Kept only if it beats
the live rules in BOTH the first two years and the held-out year. One setting,
not swept, not re-tuned after the result.

    python3 trend_room_study.py [out.json]
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
import skills_study as ss

INDICES = rs.INDICES


def run(flag):
    config.TREND_DAY_ROOM = flag
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    rows = []
    for k in INDICES:
        df = hists[k]
        F = rs.features(df, vix, nrv)
        ready = F["or_ready"].to_numpy()
        c = df["Close"].to_numpy()
        rsi = ind.rsi(df["Close"], 14).to_numpy()
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": c,
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
             "days": set(df.index.date)}
        gate = (lambda i, r, ready=ready, c=c, rsi=rsi: bool(ready[i]) and ss.rr_ok(r)
                and not ss.divergence(c, rsi, i, ps._side(r)))
        res = bt.run(k, df, gate=gate)
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F["sigma"].to_numpy()
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            p = ps.price(k, df, A, i, tr, ps.simulate(A, i, tr), s)
            if p:
                rows.append(p)
        print(f"[TREND_DAY_ROOM={flag}] {k}: {len(res['trades'])} trades", flush=True)
    return rows


def st(xs):
    s = ps.stats(xs)
    return None if not s else {"n": s["n"], "total": round(s["total"]), "avg": round(s["avg"]),
                               "pf": round(s["pf"], 2), "dd": round(s["dd"]), "win": round(s["win"], 1)}


def summary(rows):
    out = {}
    for per, f in (("in_sample", lambda r: r["when"] < rs.SPLIT), ("held_out", lambda r: r["when"] >= rs.SPLIT)):
        part = [r for r in rows if f(r)]
        out[per] = {"all": st(part), **{k: st([r for r in part if r["index"] == k]) for k in INDICES},
                    "Nifty+Sensex": st([r for r in part if r["index"] != "BANKNIFTY"]),
                    "expiry": st([r for r in part if r["expiry_day"]]),
                    "other": st([r for r in part if not r["expiry_day"]])}
    return out


def main():
    was = getattr(config, "TREND_DAY_ROOM", False)
    try:
        live, trend = summary(run(False)), summary(run(True))
    finally:
        config.TREND_DAY_ROOM = was
    di = trend["in_sample"]["all"]["total"] - live["in_sample"]["all"]["total"]
    do = trend["held_out"]["all"]["total"] - live["held_out"]["all"]["total"]
    out = {"live": live, "trend_day_room": trend,
           "verdict": {"in_sample_vs_live": di, "held_out_vs_live": do, "keep": di > 0 and do > 0}}
    print(json.dumps(out, indent=1))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
