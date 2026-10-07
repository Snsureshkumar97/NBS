#!/usr/bin/env python3
"""
mix_study.py — the tool's system and the pasted Trend Rider together, as a double confirmation (3 years)
================================================================================
The user, 7 Oct 2026, asked how the Trend Rider should run beside the tool: "what if we mix both how will be the
result for double confirmation". Two ways to mix, both measured:

  A. the TOOL'S trades and exits, taken only when the Trend Rider's conditions also hold at that candle (close above
     EMA 50 and VWAP for a call - below for a put - ADX(14) > 20 and rising, +DI over -DI / -DI over +DI)
  B. the TREND RIDER'S trades and exits (stop on the last 5 candles' low/high, target 2.5 or 2.75 x the risk, out by
     15:15), entered on the first candle where BOTH agree: its conditions hold AND the tool's own read of that candle
     (reversal_exit_study.bar_bias - build_recommendation at every bar) is the same side

Against each on its own: the tool +437,509 / +125,227 per lot; the Trend Rider (15-min, ADX 20, last 5 candles, 2.5R)
+544,405 / +142,079 (pasted_combos_study.py; checked here). Nifty, Bank Nifty, Sensex as every study; Midcap Select
(added to the tool 7 Oct, paper only, monthly options) shown on its own lines. Measured as stop_day_study.py /
pasted_combos_study.py: per lot after costs, real expiries, in-sample to 15 Aug 2025 then the held-out final year.

    python3 mix_study.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import opening_window_study as ows
import pasted_combos_study as pc
import reversal_exit_study as res
import stop_day_study as sds

INDICES = sds.INDICES
RRS = (2.5, 2.75)


def _job(k):
    if k == "MIDCPNIFTY":                                   # its own history, monthly expiries (index_candidates_study)
        import index_candidates_study as ic
        import pro_study as ps
        import backtest_intraday as bt
        import regime_study as rs
        import indicators as ind
        import math
        real = ps._scheduled
        ps._scheduled = lambda key, d: ic._monthly(d) if key == k else real(key, d)
        df = bt.fetch_history(k, years=3, use_cache=True)
        nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
        nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
        F = rs.features(df, rs.load_vix(), nrv)
        A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
             "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
        pre = bt.precompute(df, k)
        st = ind.supertrend(df, *config.supertrend_params(k))[0].to_numpy()
        live = lambda i, rec: res.live_gate(k, i, rec, df)
        target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    else:
        c = sds._context()
        df, F, A, pre, st, live, target = (c["hists"][k], c["F"][k], c["A"][k], c["pre"][k], c["st"][k], c["live"][k],
                                           c["target"])
    days = set(df.index.date)
    sig = F["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    d15 = df[["Open", "High", "Low", "Close", "Volume"]]
    tr_long, tr_short = pc.signals(d15, 2, 20.0)
    out = {"tool": ows.candidates(k, df, live, A, F, pre, st, target, True)}
    # A: the tool's trades, confirmed by the Trend Rider's state at the signal candle
    gate = lambda i, rec: (tr_long[i] if rec["option_type"] == "CE" else tr_short[i]) and live(i, rec)
    out["A"] = ows.candidates(k, df, gate, A, F, pre, st, target, True)
    # the Trend Rider alone, and B: entered where both first agree
    _, opt = res.bar_bias(k, df)
    both_l = tr_long & np.array([o == "CE" for o in opt])
    both_s = tr_short & np.array([o == "PE" for o in opt])
    for rr in RRS:
        out[("TR", rr)] = pc.run(k, d15, sigma_by_day, days, 2, 15, 20.0, rr, 5)
        out[("B", rr)] = pc.run(k, d15, sigma_by_day, days, 2, 15, 20.0, rr, 5, sig=(both_l, both_s))
    agree = float(np.mean([(o == "CE" and tl) or (o == "PE" and ts) for o, tl, ts in zip(opt, tr_long, tr_short) if o]))
    return k, out, agree


def main():
    from concurrent.futures import ProcessPoolExecutor
    print("Replaying the tool and the Trend Rider, alone and mixed, on all four indices...", flush=True)
    with ProcessPoolExecutor(max_workers=4) as ex:
        got = {k: (out, agree) for k, out, agree in ex.map(_job, list(INDICES) + ["MIDCPNIFTY"])}

    def pool(key, keys=INDICES):
        rows = []
        for k in keys:
            rows += got[k][0][key]          # already walked one position per index (ows.candidates / pc.run)
        return rows
    tool = pool("tool")
    t = sds.split(tool)
    assert (round(t["is"]["total"]), round(t["oos"]["total"])) == (437509, 125227), (t["is"]["total"], t["oos"]["total"])
    tr = sds.split(pool(("TR", 2.5)))
    assert (round(tr["is"]["total"]), round(tr["oos"]["total"])) == (544405, 142079), (tr["is"]["total"], tr["oos"]["total"])

    print("=" * 172)
    print(" NIFTY + Bank Nifty + Sensex, per lot, after costs, real expiries.  columns: trades, won, total, profit factor, "
          "worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print("-" * 172)
    print(sds.line("the tool alone (live today)", tool))
    for rr in RRS:
        print(sds.line(f"the Trend Rider alone, {rr}R", pool(("TR", rr))))
    print("-" * 172)
    print(sds.line("A. tool's trades, only when Trend Rider agrees", pool("A")))
    for rr in RRS:
        print(sds.line(f"B. Trend Rider's trades, both agree, {rr}R", pool(("B", rr))))
    print("-" * 172)
    print(" MIDCAP SELECT ALONE (monthly options, paper only in the tool)")
    for name, key in (("the tool alone", "tool"), ("the Trend Rider alone, 2.75R", ("TR", 2.75)),
                      ("A. tool's trades, Trend Rider agrees", "A"), ("B. Trend Rider's trades, both agree, 2.75R", ("B", 2.75))):
        print(sds.line("  " + name, pool(key, ["MIDCPNIFTY"])))

    print("\n VERDICT vs the tool alone - more profit in BOTH periods? (worst drop / worst day: minus = better)")
    for name, key in (("A. tool + Trend Rider agreeing", "A"), ("B. both agree, 2.5R", ("B", 2.5)), ("B. both agree, 2.75R", ("B", 2.75)),
                      ("the Trend Rider alone, 2.5R", ("TR", 2.5)), ("the Trend Rider alone, 2.75R", ("TR", 2.75))):
        print(sds.verdict(name, pool(key), tool))
    print("\n EACH INDEX ON ITS OWN - the tool vs the Trend Rider (2.75R) vs B (both agree, 2.75R): in-sample / held-out per lot")
    for k in list(INDICES) + ["MIDCPNIFTY"]:
        cells = []
        for key in ("tool", ("TR", 2.75), ("B", 2.75)):
            s_ = sds.split(pool(key, [k]))
            cells.append(f"{s_['is']['total']:>+10,.0f} / {s_['oos']['total']:>+9,.0f}")
        print(f"   {k:11s} tool {cells[0]}    Trend Rider {cells[1]}    both agree {cells[2]}")
    print("\n HOW OFTEN THEY AGREE: of the candles where the tool reads a side, the Trend Rider's conditions point the same way: "
          + ", ".join(f"{k} {100 * got[k][1]:.0f}%" for k in list(INDICES) + ["MIDCPNIFTY"]))


if __name__ == "__main__":
    main()
