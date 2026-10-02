#!/usr/bin/env python3
"""
rsi_cap_study.py — a hard RSI cap on entries, against TODAY'S real live system
================================================================================
The user, 2 Oct 2026, pasting an AI-generated strategy note: "do not enter a
new long position if the RSI is above 65. Instead, wait for a minor cooling-
off period or a pullback to the EMA20 on a shorter timeframe," then "yes" to
backtesting it.

WHAT THE TOOL DOES TODAY - NOT THE SAME THING
    The RSI VOTE already goes quiet at the extremes: signal_engine scores +1
    only while RSI_BULL_MIN (50) < RSI < RSI_OVERBOUGHT (75), -1 only while
    RSI_OVERSOLD (25) < RSI < RSI_BEAR_MAX (50). Above 75 it ABSTAINS - it
    stops voting bullish, but three other agreeing votes can still fire a
    trade. A hard cap is a VETO: no entry at all while RSI is over the line,
    whatever the other votes say. That is the new thing tested here.

HOW "WAIT FOR A COOLING-OFF PERIOD" IS MODELLED (faithfully, not approximated)
    backtest_intraday.run() applies a gate INSIDE its bar loop, so a vetoed
    bar consumes nothing - the signal simply has to be re-earned on a later
    bar, once RSI has cooled back under the line, with every other gate still
    passing. That is exactly "wait for it to cool off, then enter."
    NOT MODELLED: "a pullback to the EMA20 on a shorter timeframe" - that
    needs a shorter timeframe than the 15-minute history this project has,
    and is a different rule from an RSI cap in any case. Flagged, not faked.

THE PASTE ONLY NAMES LONGS. A cap on CALLS alone is tested exactly as pasted;
the symmetric version (also no PUT while RSI is under 100 - cap) is the
natural reading of "don't chase", so it is tested too, and a sweep of the
line itself (60/65/70/75) so one lucky threshold can't carry the verdict.

AGAINST TODAY'S REAL SYSTEM (same as adx_gate_25_study.py / di_vote_study.py):
sequential one-open-position-per-index, the deployed conditional cooldown
waiver, and the real exit - the T1-ratchet plus the deployed Supertrend trail.

    python3 rsi_cap_study.py
"""
import math

import numpy as np
import pandas as pd

import adx_gate_25_study as asg
import backtest_intraday as bt
import conditional_cooldown_study as ccs
import config
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
SPLIT = rs.SPLIT


def rsi_gate(base, rsi_arr, ce_max, pe_min):
    """base gate AND the RSI veto. ce_max: a CALL is refused while RSI is
    above it (None = no cap on calls). pe_min: a PUT is refused while RSI is
    below it (None = no cap on puts). Reads the RSI AT the signal bar - the
    same RSI backtest_intraday.tech_at() hands the engine, nothing later."""
    def gate(i, r):
        if not base(i, r):
            return False
        v = rsi_arr[i]
        if v != v:
            return True
        if ps._side(r) == "CE":
            return ce_max is None or v <= ce_max
        return pe_min is None or v >= pe_min
    return gate


def stats_of(rows):
    return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
            "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}


def evaluate(ce_max, pe_min, hists, F, A, pre_by_index, st_arr, target_key):
    gates = {}
    for k in INDICES:
        base = rs.make_gates(F[k])["opening-range break"]
        gates[k] = rsi_gate(base, pre_by_index[k]["rsi"].to_numpy(), ce_max, pe_min)
    per_index = asg.build_candidates(hists, F, A, pre_by_index, st_arr, gates, target_key)
    kept = []
    for k in INDICES:
        kept.extend(ccs.sequential_trades_conditional(
            per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
            waive_adx_threshold=config.ADX_TREND_THRESHOLD))
    return stats_of(kept), kept


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A, pre_by_index, st_arr = {}, {}, {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
        pre_by_index[k] = bt.precompute(df, k)
        st_len, st_mult = config.supertrend_params(k)
        st_line, _ = ind.supertrend(df, st_len, st_mult)
        st_arr[k] = st_line.to_numpy()
    target_key = str(getattr(config, "EXIT_AT_TARGET", "T3") or "T3").lower()

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:36s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 130)
    print(" NIFTY + Bank Nifty + Sensex, real expiries, after costs - TRUE live exit "
          "(T1-ratchet + deployed Supertrend trail), deployed cooldown waiver")
    print("=" * 130)
    print(f" {'entry rule':36s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
          f"  | OUT-OF-SAMPLE (held-out final year)")
    print("-" * 130)

    args = (hists, F, A, pre_by_index, st_arr, target_key)
    base, _ = evaluate(None, None, *args)
    print(line("today (no RSI veto)", base))

    variants = [("calls only: no CE while RSI > 65 (as pasted)", 65, None),
                ("both: no CE > 65, no PE < 35", 65, 35)]
    for hi in (60, 70, 75):
        variants.append((f"both: no CE > {hi}, no PE < {100 - hi}", hi, 100 - hi))
    results = {}
    for name, ce_max, pe_min in variants:
        results[name], _ = evaluate(ce_max, pe_min, *args)
        print(line(name, results[name]))

    print("\n VERDICT — kept only if better than today, BOTH periods")
    for name, r in results.items():
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        print(f"   {'KEEP ' if (di > 0 and do > 0) else 'drop '} {name:46s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    # What the vetoed trades actually were: today's own sequential trades,
    # bucketed by the RSI at entry. If chasing hurts, the stretched bucket
    # (calls above 65 / puts below 35) should be the weak one - IN BOTH periods.
    print("\n THE TRADES TODAY'S SYSTEM TAKES, BY RSI AT ENTRY (is the stretched bucket really the weak one?)")
    print(f" {'bucket':34s} {'IN-SAMPLE: n, total, avg/trade':38s}  | HELD-OUT")
    # The priced rows carry no RSI, so rebuild today's own sequential trades per
    # index (cheap - same candidates, same sequencing as the baseline row above)
    # and look the RSI up at each trade's entry bar.
    per_index = asg.build_candidates(hists, F, A, pre_by_index, st_arr,
                                     {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}, target_key)
    rows = []
    for k in INDICES:
        for r in ccs.sequential_trades_conditional(
                per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                waive_adx_threshold=config.ADX_TREND_THRESHOLD):
            # pro_study.price() stamps "when" at the bar's CLOSE (index + 15 min, "entry
            # at the bar's close"), so the signal bar - the one whose RSI the engine
            # actually saw - is a bar earlier.
            rows.append((r, float(pre_by_index[k]["rsi"].loc[r["when"] - pd.Timedelta(minutes=15)])))
    def bucket(side, lo, hi):
        sel = [r for r, v in rows if r["side"] == side and lo < v <= hi]
        return stats_of(sel)
    def show(label, s):
        a, b = s["is"], s["oos"]
        avg = lambda x: (x["total"] / x["n"]) if x["n"] else 0.0
        print(f" {label:34s} {a['n']:>5} ₹{a['total']:>+10,.0f}  ₹{avg(a):>+7,.0f}/trade"
              f"  | {b['n']:>4} ₹{b['total']:>+9,.0f}  ₹{avg(b):>+7,.0f}/trade")
    show("CALLS, RSI 50-55", bucket("CE", 50, 55))
    show("CALLS, RSI 55-65", bucket("CE", 55, 65))
    show("CALLS, RSI 65-75 (the pasted cap's target)", bucket("CE", 65, 75))
    show("PUTS,  RSI 45-50", bucket("PE", 45, 50))
    show("PUTS,  RSI 35-45", bucket("PE", 35, 45))
    show("PUTS,  RSI 25-35 (mirror of the cap)", bucket("PE", 25, 35))
    return {"base": base, **results}


if __name__ == "__main__":
    main()
