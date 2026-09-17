#!/usr/bin/env python3
"""
eth_study.py — should the tool issue ETH tickets?
=================================================
PRE-DECLARED 16 Sep 2026, before this was run, at the user's request to add ETH.

Why ETH: of Deribit's seven option markets it is the only one besides BTC with
real depth ($4.9B open interest, 11 expiries against SOL's $128M and 5) and it is
settled in the coin, like BTC, so the tool's existing premium conversion applies.

The test. ETH and BTC go through the identical pipeline, so ETH is judged by the
same yardstick as the market the tool already trades:

  - 15-minute candles of the perpetual from Deribit's chart endpoint, three years.
  - The signal engine as it runs live on crypto: T3 at least 1x the stop,
    trend-day room on. The opening-range wait and the RSI-divergence skip are
    Indian-indices only in tickets.py, so they are off here too. The spread hold
    cannot be tested - Deribit publishes no historical quotes - so it is not.
  - Exit at T2, stop tested first inside a bar, held at most 96 bars (24 hours),
    no square-off - crypto has no close. Results in R: multiples of the stop.
  - Two views: every signal (signal quality), and at most MAX_TRADES_PER_DAY
    entries a day in time order (closer to what an account would see).
  - Costs: 0.15R a trade, the convention the BTC figures were read with. It is
    not calibrated to Deribit; 0 and 0.25R are shown beside it.
  - First two years before 15 Aug 2025, held-out year after.

ETH is allowed to issue tickets only if, in the capped view, its average R after
0.15R of costs is ABOVE ZERO in BOTH periods. One setting, not swept. If BTC
itself does not clear that bar, that is reported as found, not smoothed over.

    python3 eth_study.py [out.json]

RESULT, 3 years of 15-minute candles to 17 Sep 2026 (ETH) / 11 Sep 2026 (BTC),
average R per trade, capped at 4 a day - the view the decision was declared on:
                          first two years                held-out year
                     n     no cost  0.15R   0.25R    n     no cost  0.15R   0.25R
  ETH              2,769   +0.021  -0.129  -0.229  1,564   +0.054  -0.096  -0.196
  BTC              2,747   +0.033  -0.117  -0.217  1,548   +0.092  -0.058  -0.158
ETH FAILS: below zero after 0.15R in both periods (PF 0.84 | 0.89, win 29% | 24%).
Not added to the live tool.
BTC FAILS THE SAME BAR, found in the same run: it clears costs in neither period
either (PF 0.85 | 0.93). Before any cost both edge out a sliver, BTC's the larger;
every signal uncapped reads the same way (ETH -0.084 | -0.101, BTC -0.026 |
-0.071 after 0.15R). The 0.15R cost is a convention, not Deribit-calibrated - but
BTC's first two years made +0.033R a trade before any cost at all, so no
realistic cost leaves it positive there. Reported to the user; BTC's live status
is theirs to decide.
"""
import json
import sys

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs
import skills_study as ss

# ETH as it would be configured - defined here, in this process only, so the
# live tool is untouched unless it passes. Specs read from Deribit on 16 Sep
# 2026: coin-settled, 1-ETH contracts, minimum order 1, strikes 20-100 apart
# near the money depending on the expiry.
ETH = {
    "yahoo_ticker": "ETH-USD", "nse_symbol": None, "kite_exchange": None,
    "kite_tradingsymbol": None, "market": "crypto", "provider": "deribit",
    "deribit_instrument": "ETH-PERPETUAL", "deribit_index": "eth_usd",
    "deribit_currency": "ETH", "quote_ccy": "USD", "strike_step": 50,
    "lot_size": 1, "qty_step": 1, "has_free_option_chain": False,
}
config.INSTRUMENTS.setdefault("ETH", ETH)

COSTS = (0.0, 0.15, 0.25)
DECIDE_COST = 0.15
CAP = int(getattr(config, "MAX_TRADES_PER_DAY", 4))


def trades_for(key):
    df = bt.fetch_history(key, years=3, use_cache=True)
    res = bt.run(key, df, gate=lambda i, r: ss.rr_ok(r))
    dates = pd.Series(df.index.date)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (dates != dates.shift(-1)).to_numpy(), "days": set(df.index.date)}
    pos = {t: n for n, t in enumerate(df.index)}
    rows = []
    for tr in res["trades"]:
        i = pos[tr["when"]]
        legs = ps.simulate(A, i, tr, hold_bars=96, square_off=False, target="t2")
        ce = tr["side"] == "CE"
        r = sum(w * ((px - tr["entry"]) if ce else (tr["entry"] - px)) for px, _bar, w in legs) / tr["risk"]
        rows.append({"when": pd.Timestamp(tr["when"]), "r": float(r), "side": tr["side"]})
    rows.sort(key=lambda x: x["when"])
    return rows, {"candles": len(df), "from": str(df.index[0]), "to": str(df.index[-1])}


def capped(rows, n=CAP):
    out, per = [], {}
    for x in rows:
        d = x["when"].date()
        if per.get(d, 0) < n:
            out.append(x); per[d] = per.get(d, 0) + 1
    return out


def stats(rows, cost):
    if not rows:
        return None
    r = np.array([x["r"] - cost for x in rows])
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    curve = np.concatenate([[0.0], np.cumsum(r)])
    dd = float((np.maximum.accumulate(curve) - curve).max())
    return {"n": int(len(r)), "avg_r": round(float(r.mean()), 3), "total_r": round(float(r.sum()), 1),
            "win_pct": round(float((r > 0).mean() * 100), 1),
            "pf": round(float(wins / losses), 2) if losses else None, "worst_dd_r": round(dd, 1)}


def main():
    out = {"declared": {"decide_on": "capped", "cost_r": DECIDE_COST, "cap_per_day": CAP,
                        "split": str(rs.SPLIT)}, "markets": {}}
    for key in ("ETH", "BTC"):
        rows, meta = trades_for(key)
        views = {"all signals": rows, f"capped {CAP}/day": capped(rows)}
        res = {"history": meta}
        for vname, vrows in views.items():
            res[vname] = {}
            for per, f in (("first_two_years", lambda x: x["when"] < rs.SPLIT),
                           ("held_out_year", lambda x: x["when"] >= rs.SPLIT)):
                part = [x for x in vrows if f(x)]
                res[vname][per] = {f"cost_{c}R": stats(part, c) for c in COSTS}
        cap = res[f"capped {CAP}/day"]
        res["passes"] = all((cap[p][f"cost_{DECIDE_COST}R"] or {}).get("avg_r", -1) > 0
                            for p in ("first_two_years", "held_out_year"))
        out["markets"][key] = res
        print(f"[{key}] {meta['candles']:,} candles, {len(rows):,} signals, passes: {res['passes']}", flush=True)
    out["verdict"] = {"add_eth_tickets": out["markets"]["ETH"]["passes"],
                      "btc_clears_the_same_bar": out["markets"]["BTC"]["passes"]}
    print(json.dumps(out, indent=1, default=str))
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
