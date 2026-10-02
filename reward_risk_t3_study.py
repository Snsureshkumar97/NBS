#!/usr/bin/env python3
"""
reward_risk_t3_study.py — sweep config.MIN_REWARD_RISK_T3, Indian indices
================================================================================
The user, 2 Oct 2026: "back test on different risk reward for indian market
check which one is best."

A REAL GAP FOUND WHILE BUILDING THIS, WORTH BEING PLAIN ABOUT
    config.MIN_REWARD_RISK_T3 (today 1.0 for NSE) is enforced live in
    tickets.py's _reward_hold() via the module-level reward_risk_t3() - a
    REAL gate, checked on every fresh entry AND every re-arm. pro_study.py's
    own "LIVE RULES (OR break)" baseline, used by EVERY study this project
    has ever run through bt.run(), NEVER calls into tickets.py at all - it
    has NEVER enforced this gate. The one place pro_study.py even gestures
    at it ("min reward:risk 1 (T3)") hand-rolls its own approximation,
    hard-coded to 1.0, not reading config or calling the real function.
    This does not retroactively invalidate any PAST study - each one still
    compared an idea against the SAME (gate-less) baseline, so those
    comparisons were apples-to-apples - but it means no study in this
    project has ever actually measured what today's REAL reward:risk floor
    costs or earns, until now.

THIS STUDY calls tickets.reward_risk_t3() directly - the exact live
function, not a re-implementation - so "today" here means what tickets.py
actually enforces, not an approximation of it. The baseline below is
LIVE RULES (OR break) PLUS that real gate at today's value (1.0) - the
first time this project's baseline has actually matched what a real
ticket requires. Every other threshold is swept against THAT, not against
the gate-less raw baseline every other study in this file uses.

    python3 reward_risk_t3_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs
import tickets

INDICES = rs.INDICES
SPLIT = rs.SPLIT


TARGET_NAMES = ("T1", "T2", "T3")


def rr_to_target(rec, target_idx):
    """abs(target - spot) / risk, for target_idx 0/1/2 = T1/T2/T3 - the same
    formula tickets.reward_risk_t3() uses for T3 specifically (target_idx=2
    here reproduces it exactly), generalised to T1/T2 because NEITHER of
    those is an actual live gate anywhere in tickets.py - this is purely
    exploratory, asked for directly: does gating on a CLOSER target's own
    reward:risk do better than gating on T3's, the only one actually live.
    T1 sits at 40% of the room, T2 at 70%, T3 at 100% (tickets.py's own
    placement) - so the SAME numeric threshold means something very
    different depending which target it's measured against."""
    spot, risk = rec.get("spot"), rec.get("risk_points")
    tg = rec.get("index_targets") or [None, None, None]
    t = tg[target_idx] if len(tg) > target_idx else None
    if spot is None or not risk or t is None:
        return None
    return abs(t - spot) / risk


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}

    def rr_gate(k, threshold):
        def g(i, r, k=k, threshold=threshold):
            if not base_gate[k](i, r):
                return False
            rr, _ = tickets.reward_risk_t3(k, r)
            return rr is not None and rr >= threshold
        return g

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
        return (f" {name:28s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 128)
    print(" NIFTY + Bank Nifty + Sensex, real expiries, after costs - LIVE RULES (OR break) "
          "PLUS the real reward_risk_t3() gate (not a re-implementation)")
    print("=" * 128)
    hdr = (f" {'variant':28s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)

    was = dict(config.MIN_REWARD_RISK_T3)
    out = {}
    try:
        for thresh in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
            config.MIN_REWARD_RISK_T3["nse_index"] = thresh
            gate = {k: rr_gate(k, thresh) for k in INDICES}
            r = evaluate(gate)
            tag = " (today's live value)" if thresh == 1.0 else ""
            name = f"T3 R:R >= {thresh:g}{tag}"
            out[name] = r
            print(line(name, r))
    finally:
        config.MIN_REWARD_RISK_T3.clear()
        config.MIN_REWARD_RISK_T3.update(was)

    base = out["T3 R:R >= 1 (today's live value)"]
    print("\n VERDICT — kept only if better than today's live value (1.0), both periods")
    for name, r in out.items():
        if name.startswith("T3 R:R >= 1 ("):
            continue
        di = r["is"]["total"] - base["is"]["total"]; do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {name:28s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    print("\n" + "=" * 128)
    print(" WHICH TARGET'S OWN REWARD:RISK IS THE BETTER GATE - T1/T2 are NOT live gates "
          "anywhere today, purely exploratory")
    print("=" * 128)
    print(hdr); print("-" * 128)
    print(line("T3 R:R >= 1 (today's live value)", base))
    out2 = {"T3 R:R >= 1 (today's live value)": base}
    for target_idx, target_name in enumerate(TARGET_NAMES):
        for thresh in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0):
            if target_name == "T3" and thresh == 1.0:
                continue        # already the baseline above
            gate = {}
            for k in INDICES:
                def gk(i, r, k=k, target_idx=target_idx, thresh=thresh):
                    if not base_gate[k](i, r):
                        return False
                    rr = rr_to_target(r, target_idx)
                    return rr is not None and rr >= thresh
                gate[k] = gk
            r = evaluate(gate)
            name = f"{target_name} R:R >= {thresh:g}"
            out2[name] = r
            print(line(name, r))

    print("\n VERDICT (by target) — kept only if better than today's live T3 R:R >= 1, both periods")
    for name, r in out2.items():
        if name.startswith("T3 R:R >= 1 ("):
            continue
        di = r["is"]["total"] - base["is"]["total"]; do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'drop '} {name:28s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    return out, out2


if __name__ == "__main__":
    main()
