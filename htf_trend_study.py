#!/usr/bin/env python3
"""
htf_trend_study.py — robustness: the 1h-trend EMA pair, and the entry ADX gate itself
================================================================================
Two sweeps, requested by the user 2 Oct 2026 right after "live + 1h trend
agrees" was added to pro_study.py's entry_variants - "also backtest with
different adx gates and different EMA not only 20 50."

SWEEP 1 - the EMA pair behind the 1-hour trend read (pro_study.py's own
    "live + 1h trend agrees" hard-codes config.EMA_FAST/EMA_SLOW (20/50),
    the SAME pair the 15-MINUTE trend vote already uses. There is no reason
    a 1-HOUR read needs the same pair - swept here, not assumed.

SWEEP 2 - the entry ADX gate threshold itself. NOTE: this is
    config.strictness()["adx"] (today 20, via the "strict" tier in
    config._STRICTNESS), NOT config.ADX_TREND_THRESHOLD - that second
    setting only feeds compute_market_trend()'s STRONG/MODERATE/WEAK
    display label and an unused tech["adx_ok"] field build_recommendation()
    never reads; confirmed by reading every line that touches either name.
    Swept independently of sweep 1 (not a joint grid) - this project's own
    convention for checking one variable at a time; a joint sweep is a
    natural next step if either shows promise on its own.

    python3 htf_trend_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
SPLIT = rs.SPLIT


def htf_dir_for(cl, fast, slow):
    """pro_study.extra_features()'s own htf_dir computation, factored out so
    it can be re-run cheaply for many (fast, slow) pairs without re-running
    the rest of extra_features() (Bollinger/Stochastic/CCI/etc, unneeded
    for this sweep) each time."""
    htf = cl.resample("1h").last().dropna()
    ema_f, ema_s = ind.ema(htf, fast), ind.ema(htf, slow)
    d = pd.Series(0, index=htf.index)
    d[(htf > ema_s) & (ema_f > ema_s)] = 1
    d[(htf < ema_s) & (ema_f < ema_s)] = -1
    return d.shift(1).reindex(cl.index, method="ffill").to_numpy()


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}

    def evaluate(gate_by_index):
        rows = []
        for k in INDICES:
            df = hists[k]
            res = bt.run(k, df, gate=gate_by_index[k])
            sig = F[k]["sigma"].to_numpy()
            pos = {t: n for n, t in enumerate(df.index)}
            A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                 "cl": df["Close"].to_numpy(),
                 "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                 "days": set(df.index.date)}
            for tr in res["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A, i, tr)
                p = ps.price(k, df, A, i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:30s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    hdr = (f" {'variant':30s} {'IN-SAMPLE':50s}  | OUT-OF-SAMPLE (held-out)")

    # pro_study.py's own "R:R 1 + Bank Nifty watch-only" baseline, reproduced
    # directly (that entry_variants dict is a local inside pro_study.main(),
    # not exposed at module level to import) - the SAME baseline every other
    # textbook-filter variant in that file (Bollinger, SAR, CCI, ...) is
    # measured against, for apples-to-apples comparison with them. NOTE this
    # is an older, Sep-2026 baseline (Bank Nifty fully excluded) - today's
    # LIVE WATCH_ONLY_INDICES is actually empty; kept matching pro_study.py's
    # own convention rather than today's live config, consistency with every
    # sibling study in that file matters more here than matching live exactly.
    baseline_gate = {k: (lambda i, r, k=k:
        k != "BANKNIFTY" and base_gate[k](i, r) and bool(r.get("risk_points"))
        and r["index_targets"][2] is not None
        and abs(r["index_targets"][2] - r["spot"]) >= r["risk_points"]) for k in INDICES}
    baseline = evaluate(baseline_gate)

    print("=" * 110)
    print(" SWEEP 1: the 1-hour trend's own EMA pair (today's filter uses config.EMA_FAST/SLOW, 20/50)")
    print("=" * 110)
    print(hdr); print("-" * 110)
    print(line("baseline (R:R 1 + BN watch-only, no htf filter)", baseline))
    out1 = {}
    for fast, slow in ((5, 15), (10, 20), (12, 26), (20, 50), (50, 100)):
        htf = {k: htf_dir_for(hists[k]["Close"], fast, slow) for k in INDICES}
        gate = {k: (lambda i, r, k=k, htf=htf: baseline_gate[k](i, r) and (
            (ps._side(r) == "CE" and htf[k][i] == 1) or
            (ps._side(r) == "PE" and htf[k][i] == -1))) for k in INDICES}
        r = evaluate(gate)
        name = f"1h trend agrees, EMA {fast}/{slow}"
        out1[name] = r
        print(line(name, r))

    print("\n VERDICT (sweep 1) — kept only if better than the no-filter baseline, both periods")
    for name, r in out1.items():
        di = r["is"]["total"] - baseline["is"]["total"]; do = r["oos"]["total"] - baseline["oos"]["total"]
        dd = r["oos"]["dd"] - baseline["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {name:30s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    print("\n" + "=" * 110)
    print(" SWEEP 2: the entry ADX gate threshold itself (config.strictness()['adx'], today 20 - "
          "NOT config.ADX_TREND_THRESHOLD, which only drives a display label)")
    print("=" * 110)
    print(hdr); print("-" * 110)
    print(line("baseline (ADX gate = 20, today's live value)", baseline))
    was_adx = config._STRICTNESS["strict"]["adx"]
    out2 = {}
    try:
        for adx in (12, 15, 18, 20, 22, 25, 30):
            config._STRICTNESS["strict"]["adx"] = adx
            r = evaluate(baseline_gate)
            name = f"ADX gate = {adx}"
            out2[name] = r
            print(line(name, r))
    finally:
        config._STRICTNESS["strict"]["adx"] = was_adx

    print("\n VERDICT (sweep 2) — kept only if better than today's ADX=20, both periods")
    for name, r in out2.items():
        if name.endswith("= 20"):
            continue
        di = r["is"]["total"] - baseline["is"]["total"]; do = r["oos"]["total"] - baseline["oos"]["total"]
        dd = r["oos"]["dd"] - baseline["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {name:30s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    return {"baseline": baseline, "htf_ema_sweep": out1, "adx_sweep": out2}


if __name__ == "__main__":
    main()
