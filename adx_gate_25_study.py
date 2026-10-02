#!/usr/bin/env python3
"""
adx_gate_25_study.py — raise the entry ADX gate from 20 to 25, against TODAY'S
real live system, not an old baseline
================================================================================
The user, 2 Oct 2026: "change the adx gate to above 25 and check the backtest
results." This is config.strictness()["adx"] (today 20, via the "strict" tier
in config._STRICTNESS) - NOT config.ADX_TREND_THRESHOLD, which only drives
compute_market_trend()'s display label and an unused tech["adx_ok"] field
build_recommendation() never reads for its own bias decision (confirmed by
reading every line that touches either name, same as htf_trend_study.py).

WHY NOT JUST REUSE htf_trend_study.py's OWN ADX SWEEP (it already covered 25)
    That sweep (i) priced every candidate with pro_study.simulate()'s PLAIN
    mode - the flat-stop baseline supertrend_trail_study.py later found does
    NOT match what tickets.py's _check_price() actually does (the real,
    unconditional T1-ratchet was never modelled) - its own numbers understate
    both profit and drawdown-protection; (ii) let candidates overlap freely
    on the same index (no one-open-position exclusivity, the gap
    reentry_cooldown_study.py found and fixed); (iii) excluded Bank Nifty
    entirely from the gate as a Sep-2026 convention never updated to match
    today's live WATCH_ONLY_INDICES (empty - Bank Nifty trades normally).
    None of that invalidates ITS OWN relative ranking (every ADX value in
    that sweep shared the same baseline) - but the user is asking about TODAY,
    and today's live system does all three of exclusivity, the real T1-
    ratchet, the deployed Supertrend trail, and the deployed conditional
    cooldown waiver. This file rebuilds the comparison against ALL of that.

WHAT "TODAY'S REAL LIVE SYSTEM" MEANS HERE, PRECISELY
    - Entries: regime_study's "opening-range break" gate (same as every
      sibling study), riding on signal_engine's real bias/ADX/MACD/volume
      vote via backtest_intraday.bt.run() - config._STRICTNESS["strict"]
      ["adx"] is read THERE, so changing it before bt.run() changes which
      bars actually qualify, not just a label.
    - Sequencing: conditional_cooldown_study.sequential_trades_conditional()
      - one open position per index, REENTRY_COOLDOWN_MIN (20) after a
      same-direction close, WAIVED after a trending target hit (both
      deployed, live today).
    - Exit: pro_study.simulate(..., ratchet_to_t1=True,
      trail_after_t1_supertrend=<this index's own Supertrend line>) - the
      real T1-ratchet (tickets.py's _check_price(), always on) PLUS the
      Supertrend trail (config.TRAIL_AFTER_T1_SUPERTREND, deployed 2 Oct),
      each index's own (length, multiplier) via config.supertrend_params().
    - Pricing: pro_study.price() unmodified - real expiries, real Zerodha
      costs, same as every other study in this project.

    python3 adx_gate_25_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import conditional_cooldown_study as ccs
import config
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
SPLIT = rs.SPLIT


def build_candidates(hists, F, A, pre_by_index, st_arr, base_gate, target_key):
    """One priced candidate list per index, under TODAY'S real exit (T1-
    ratchet + Supertrend trail) - everything downstream (which ADX gate,
    which cooldown rule) just re-filters this same list, never re-prices."""
    per_index = {}
    for k in INDICES:
        df = hists[k]
        res = bt.run(k, df, gate=base_gate[k])
        pos = {t: n for n, t in enumerate(df.index)}
        sig = F[k]["sigma"].to_numpy()
        adx_series = pre_by_index[k]["adx"]
        rows = []
        for tr in res["trades"]:
            i = pos[tr["when"]]
            s = sig[i]
            if not (s == s) or s <= 0:
                continue
            legs = ps.simulate(A[k], i, tr, target=target_key,
                                ratchet_to_t1=True, trail_after_t1_supertrend=st_arr[k])
            p = ps.price(k, df, A[k], i, tr, legs, s)
            if p is None:
                continue
            exit_px, exit_bar, _ = legs[-1]
            if abs(exit_px - tr["stop"]) < 1e-6:
                closed_via = "stop"
            elif tr.get(target_key) is not None and abs(exit_px - tr[target_key]) < 1e-6:
                closed_via = "target"
            else:
                closed_via = "other"
            p["side"] = tr["side"]
            p["closed_via"] = closed_via
            p["exit_adx"] = float(adx_series.iloc[exit_bar])
            rows.append(p)
        rows.sort(key=lambda r: r["when"])
        per_index[k] = rows
    return per_index


def evaluate_adx(adx_value, hists, F, A, pre_by_index, st_arr, target_key):
    """Re-runs the ENTRY gate at this ADX threshold (bt.run() re-reads
    config._STRICTNESS live), then the real live sequencing on top."""
    was = config._STRICTNESS["strict"]["adx"]
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}
    try:
        config._STRICTNESS["strict"]["adx"] = adx_value
        per_index = build_candidates(hists, F, A, pre_by_index, st_arr, base_gate, target_key)
    finally:
        config._STRICTNESS["strict"]["adx"] = was

    rows = []
    for k in INDICES:
        rows.extend(ccs.sequential_trades_conditional(
            per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
            waive_adx_threshold=config.ADX_TREND_THRESHOLD))
    return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
            "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, per_index


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
          "(T1-ratchet + deployed Supertrend trail), conditional cooldown waiver (deployed)")
    print("=" * 128)
    hdr = (f" {'ADX entry gate':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)

    r20, _ = evaluate_adx(20, hists, F, A, pre_by_index, st_arr, target_key)
    print(line("ADX >= 20 (today, live)", r20))
    r25, per_index_25 = evaluate_adx(25, hists, F, A, pre_by_index, st_arr, target_key)
    print(line("ADX >= 25 (requested)", r25))

    print("\n VERDICT — kept only if ADX>=25 beats today's ADX>=20, both periods")
    di = r25["is"]["total"] - r20["is"]["total"]
    do = r25["oos"]["total"] - r20["oos"]["total"]
    dd = r25["oos"]["dd"] - r20["oos"]["dd"]
    keep = di > 0 and do > 0
    print(f"   {'KEEP ' if keep else 'drop '} ADX >= 25   in-sample {di:>+10,.0f}"
          f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    print(f"   trade count: {r20['is']['n'] + r20['oos']['n']} at ADX>=20  ->  "
          f"{r25['is']['n'] + r25['oos']['n']} at ADX>=25")

    print("\n ROBUSTNESS: a few neighbouring values, same everything else")
    for adx in (22, 28, 30):
        r, _ = evaluate_adx(adx, hists, F, A, pre_by_index, st_arr, target_key)
        print(line(f"ADX >= {adx}", r))

    return {"adx20": r20, "adx25": r25}


if __name__ == "__main__":
    main()
