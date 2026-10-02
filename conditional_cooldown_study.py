#!/usr/bin/env python3
"""
conditional_cooldown_study.py — waive the cooldown by WHY the trade closed
================================================================================
The user, 2 Oct 2026: of four brainstormed angles beyond a flat cooldown
length (see nbs-cooldown-sweep-and-methodology-fix.md for the length sweep
itself - mostly DROP, no clean trend), this is #1: a flat 20-minute
REENTRY_COOLDOWN_MIN treats every close the same. A cooldown earns its keep
after a STOP-OUT - it stops chasing straight back into a read that was just
wrong. It earns its keep much less after a clean TARGET hit in a market that
is still trending - the signal did not fail, it simply ran out of target.

THE RULE TESTED HERE (pre-declared, not tuned to the result)
    After a same-direction close:
      - closed via the STOP, or via a time/hold_bars/square-off exit (never
        reached target, never a real "it worked"): the FULL
        REENTRY_COOLDOWN_MIN (20 min, unchanged) still applies - no waiver.
      - closed by hitting the target (config.EXIT_AT_TARGET) AND, AT THAT
        CLOSING BAR, ADX >= config.ADX_TREND_THRESHOLD (20 - the EXACT
        threshold the live entry gate already uses for "is this actually
        trending", not a new number invented for this study): the cooldown
        is WAIVED (0 minutes) for the next same-direction entry.
      - closed by hitting the target but ADX had already dropped below that
        threshold (the move was fading even before close): the full
        cooldown still applies - re-entering immediately there would be
        chasing, not riding a continuation, which is exactly the case a
        cooldown should still guard against.
    One-open-position-per-index exclusivity still applies regardless -
    waiving the cooldown never allows two concurrent positions on one index.

WHY A SEPARATE FILE, NOT A CHANGE TO reentry_cooldown_study.py
    That file is already tested and its own result already reported
    (nbs-cooldown-sweep-and-methodology-fix.md). This reuses its exact
    candidate-building and sequential-exclusivity logic but needs PER-TRADE
    state (how it closed, ADX at that moment) a flat cooldown sweep never
    needed - adding that to the existing function would have made its
    already-passing tests riskier to touch for no benefit to the thing they
    already prove. sequential_trades_conditional() below is the ONLY new
    piece of logic; everything else (entries, exit pricing, costs) is
    identical to every other exit-shaped study in this project.
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs

INDICES = rs.INDICES
SPLIT = rs.SPLIT


def sequential_trades_conditional(candidates, base_cooldown_min, waive_adx_threshold):
    """candidates: one index's own trades, sorted by "when" (entry), each a
    dict with "when", "exit_time", "side", "closed_via" ("target"/"stop"/
    "other" - this trade's OWN close reason), "exit_adx" (ADX at this
    trade's OWN close). Exclusivity (one open position per index) always
    applies. A same-direction follow-up's cooldown is 0 if the LAST trade of
    that direction closed via target with exit_adx >= waive_adx_threshold,
    else base_cooldown_min - mirroring reentry_cooldown_study.sequential_trades()
    exactly, except the cooldown length is now looked up per prior-trade
    instead of being the one fixed value passed in.
    """
    open_until = None
    last = {"CE": None, "PE": None}
    kept = []
    for tr in candidates:
        if open_until is not None and tr["when"] < open_until:
            continue
        prior = last[tr["side"]]
        if prior is not None:
            waived = (prior["closed_via"] == "target" and
                      prior["exit_adx"] >= waive_adx_threshold)
            cooldown = pd.Timedelta(minutes=0 if waived else base_cooldown_min)
            if tr["when"] < prior["close"] + cooldown:
                continue
        kept.append(tr)
        open_until = tr["exit_time"]
        last[tr["side"]] = {"close": tr["exit_time"], "closed_via": tr["closed_via"],
                             "exit_adx": tr["exit_adx"]}
    return kept


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    pre_by_index = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
        pre_by_index[k] = bt.precompute(df, k)
    base_gate = {k: rs.make_gates(F[k])["opening-range break"] for k in INDICES}
    target_key = str(getattr(config, "EXIT_AT_TARGET", "T3") or "T3").lower()

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
            legs = ps.simulate(A[k], i, tr, target=target_key)
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

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    def stats_of(rows):
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    print("=" * 128)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(f" ADX trend threshold reused from the live entry gate: config.ADX_TREND_THRESHOLD = "
          f"{config.ADX_TREND_THRESHOLD}")
    print("=" * 128)
    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print(hdr); print("-" * 128)

    flat_rows = []
    for k in INDICES:
        import reentry_cooldown_study as rc
        flat_rows.extend(rc.sequential_trades(per_index[k], cooldown_min=config.REENTRY_COOLDOWN_MIN))
    flat = stats_of(flat_rows)
    print(line(f"flat {config.REENTRY_COOLDOWN_MIN}-min cooldown (today, live)", flat))

    cond_rows = []
    for k in INDICES:
        cond_rows.extend(sequential_trades_conditional(
            per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
            waive_adx_threshold=config.ADX_TREND_THRESHOLD))
    cond = stats_of(cond_rows)
    print(line("conditional: waived after a trending target hit", cond))

    waived_count = sum(1 for k in INDICES for r in per_index[k]
                        if r["closed_via"] == "target" and r["exit_adx"] >= config.ADX_TREND_THRESHOLD)
    print(f"   ({waived_count} of {sum(len(v) for v in per_index.values())} candidate closes "
          f"qualified for a waiver: closed at target with ADX >= {config.ADX_TREND_THRESHOLD})")

    print("\n VERDICT — kept only if better than today's flat 20-minute cooldown, both periods")
    di = cond["is"]["total"] - flat["is"]["total"]; do = cond["oos"]["total"] - flat["oos"]["total"]
    dd = cond["oos"]["dd"] - flat["oos"]["dd"]
    keep = di > 0 and do > 0
    print(f"   {'KEEP ' if keep else 'drop '} conditional waiver   in-sample {di:>+10,.0f}"
          f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    # Robustness: config.ADX_TREND_THRESHOLD (20) was reused, not invented for
    # this study - but one value is still one value. Sweep nearby thresholds
    # against the SAME flat-cooldown baseline to see whether the result holds
    # as a real effect or was a coincidence of exactly 20.
    print(f"\n ROBUSTNESS — the same waiver rule at nearby ADX thresholds, not just {config.ADX_TREND_THRESHOLD}")
    for thresh in (10, 15, 20, 25, 30, 35):
        rows = []
        for k in INDICES:
            rows.extend(sequential_trades_conditional(
                per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
                waive_adx_threshold=thresh))
        r = stats_of(rows)
        di = r["is"]["total"] - flat["is"]["total"]; do = r["oos"]["total"] - flat["oos"]["total"]
        dd = r["oos"]["dd"] - flat["oos"]["dd"]
        keep = di > 0 and do > 0
        tag = " (today's entry-gate value)" if thresh == config.ADX_TREND_THRESHOLD else ""
        print(f"   {'KEEP ' if keep else 'drop '} threshold {thresh:>2}{tag:30s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    print("\n" + "=" * 128)
    print(" MONTH BY MONTH, all three years - same pooled per-lot numbers as above")
    print("=" * 128)
    print_monthly(flat_rows, cond_rows)
    return {"flat": flat, "conditional": cond}


def print_monthly(flat_rows, cond_rows):
    """Net P&L and trade count per calendar month, both variants side by
    side, so a 3-year pooled total can be read month by month instead of
    only as two lumped in-sample/held-out figures."""
    def monthly(rows):
        df = pd.DataFrame(rows)
        df["month"] = pd.to_datetime(df["when"]).dt.tz_localize(None).dt.to_period("M")
        return df.groupby("month")["net"].agg(n="count", total="sum")

    f_m, c_m = monthly(flat_rows), monthly(cond_rows)
    months = sorted(set(f_m.index) | set(c_m.index))
    print(f" {'month':10s} {'flat 20-min cooldown (today, live until now)':32s}  "
          f"| {'conditional waiver (now live)':32s}  | difference")
    print("-" * 128)
    f_tot = c_tot = 0.0
    for m in months:
        fn, ft = (f_m.loc[m, "n"], f_m.loc[m, "total"]) if m in f_m.index else (0, 0.0)
        cn, ct = (c_m.loc[m, "n"], c_m.loc[m, "total"]) if m in c_m.index else (0, 0.0)
        f_tot += ft; c_tot += ct
        marker = " <- held-out" if pd.Timestamp(str(m)) >= SPLIT.tz_localize(None) else ""
        print(f" {str(m):10s} {fn:>3} trades ₹{ft:>+10,.0f}          | "
              f"{cn:>3} trades ₹{ct:>+10,.0f}          | {ct - ft:>+10,.0f}{marker}")
    print("-" * 128)
    print(f" {'TOTAL':10s} {sum(f_m['n']):>3} trades ₹{f_tot:>+10,.0f}          | "
          f"{sum(c_m['n']):>3} trades ₹{c_tot:>+10,.0f}          | {c_tot - f_tot:>+10,.0f}")


if __name__ == "__main__":
    main()
