#!/usr/bin/env python3
"""
di_vote_study.py — +DI/-DI as a vote, against TODAY'S real live system
================================================================================
The user, 2 Oct 2026, quoting an Investopedia guide that ADX is usually paired
with +DI/-DI "to know whether prices are moving up or down," then "yes" to
backtesting it. This tool's ADX has only ever been a trend-STRENGTH gate;
direction comes from the Trend/MACD/RSI/VWAP(/PCR) votes. Two readings of
"use +DI/-DI" tested, same shape as volume_vote_study.py:

    "add"           +DI/-DI joins Trend/MACD/RSI/VWAP as an extra vote
    "replace_trend" +DI/-DI takes the EMA-based Trend vote's own slot - both
                    are "which way is the trend", so this is the natural
                    swap, the way "replace_macd" was for volume

THE VOTE: +DI above -DI -> +1, -DI above +DI -> -1, equal/undefined -> 0.
No dead-band - the +DI/-DI crossing is itself the signal. Same length and
smoothing as the live ADX (config.ADX_LENGTH) so the two share one reading.

AGAINST TODAY'S REAL SYSTEM, NOT AN OLD BASELINE (same as adx_gate_25_study.py,
for the same reasons - see its docstring): sequential one-open-position-per-
index, the deployed conditional cooldown waiver, and the real exit - the
T1-ratchet plus the deployed Supertrend trail. All three NSE indices.

A TRAP THIS STUDY NEARLY FELL INTO, FIXED BEFORE ANY RESULT WAS READ:
    backtest_intraday.tech_at() builds the backtest's tech dict from
    precomputed arrays, and had no di_score in it - build_recommendation()
    would have read 0 for every bar, so "add" would have silently equalled
    the baseline and "replace_trend" would have simply deleted Trend. Both
    would have looked like results. precompute()/tech_at() now carry it, and
    verify_precompute() checks it against the real compute_technical_signal.

    python3 di_vote_study.py
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


def evaluate_mode(mode, hists, F, A, pre_by_index, st_arr, target_key):
    """Re-runs the ENTRY decision with config.DI_VOTE_MODE = mode (bt.run()
    calls the real build_recommendation(), which reads it), then the real
    live sequencing on top. Returns (pooled stats, per-index stats, trades)."""
    was = config.DI_VOTE_MODE
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}
    try:
        config.DI_VOTE_MODE = mode
        per_index = asg.build_candidates(hists, F, A, pre_by_index, st_arr, base_gate, target_key)
    finally:
        config.DI_VOTE_MODE = was

    def stats_of(rows):
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    kept, by_idx = [], {}
    for k in INDICES:
        rows = ccs.sequential_trades_conditional(
            per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
            waive_adx_threshold=config.ADX_TREND_THRESHOLD)
        by_idx[k] = stats_of(rows)
        kept.extend(rows)
    return stats_of(kept), by_idx


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
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 128)
    print(" NIFTY + Bank Nifty + Sensex, real expiries, after costs - TRUE live exit "
          "(T1-ratchet + deployed Supertrend trail), deployed cooldown waiver")
    print("=" * 128)
    hdr = (f" {'entry vote set':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)

    results, per_idx = {}, {}
    for mode, label in (("off", "today (Trend/MACD/RSI/VWAP)"),
                        ("add", "+ an extra +DI/-DI vote"),
                        ("replace_trend", "+DI/-DI instead of the EMA Trend")):
        results[mode], per_idx[mode] = evaluate_mode(mode, hists, F, A, pre_by_index, st_arr, target_key)
        print(line(label, results[mode]))

    base = results["off"]
    print("\n VERDICT — kept only if better than today's vote set, BOTH periods")
    for mode, label in (("add", "add +DI/-DI"), ("replace_trend", "replace Trend with +DI/-DI")):
        r = results[mode]
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {label:28s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    print("\n PER INDEX (in-sample / held-out total) - is any index carrying or hiding the pooled result?")
    for k in INDICES:
        cells = []
        for mode in ("off", "add", "replace_trend"):
            r = per_idx[mode][k]
            cells.append(f"{mode:>13}: {r['is']['total']:>+9,.0f} / {r['oos']['total']:>+8,.0f}")
        print(f"   {k:10s} " + "   ".join(cells))
    return results


if __name__ == "__main__":
    main()
