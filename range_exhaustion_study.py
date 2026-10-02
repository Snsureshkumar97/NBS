#!/usr/bin/env python3
"""
range_exhaustion_study.py — "stop buying options once the day's range is used up"
================================================================================
The user, 2 Oct 2026, pasting an AI-generated "room to run" framework for NSE
intraday options, then "yes" to backtesting its main rule:

    Remaining room = 14-day daily ATR - (today's high - today's low)
    "Has moved > 85% of its daily ATR: stop buying options ... the statistical
     room is completely gone."   (equivalently: remaining room < 15% of ATR)

Tested exactly as written: a VETO on any new entry (calls and puts alike) once
today's high-low so far exceeds 85% of the 14-day ATR of DAILY bars. The ATR
uses completed PREVIOUS days only (shifted one day - day D never sees its own
range in its own yardstick); today's high-low is as it stood at the signal
bar's close, nothing later. Thresholds 50/70/85/100/125% swept so one lucky
line can't carry the verdict. The paste's middle tier ("50-80%: only high-
conviction trades, tighten the stop") is too vague to test as written and is
not attempted.

NOTE, BEFORE ANY RESULT: this tool already went the OTHER way once, on its own
evidence - trend_room_study.py (commit 2fddcd2) found that giving a day that
has used its whole normal range MORE room, not less, did better in both
periods and on every index. The RSI cap (rsi_cap_study.py) pointed the same
way. This study measures the paste's rule on today's system rather than
assuming either answer.

Against TODAY'S real system (same as rsi_cap_study.py / di_vote_study.py):
sequential one-open-position-per-index, the deployed conditional cooldown
waiver, and the real exit - the T1-ratchet plus the deployed Supertrend trail.

    python3 range_exhaustion_study.py
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


def prev_day_atr(df, length=14):
    """14-day ATR of DAILY bars, as known at the START of each day - i.e.
    through yesterday only - mapped onto every intraday bar of that day."""
    day = pd.Series(df.index.date, index=df.index)
    d = df.groupby(day.values).agg(High=("High", "max"), Low=("Low", "min"),
                                   Open=("Open", "first"), Close=("Close", "last"))
    atr_prev = ind.atr(d, length).shift(1)
    return day.map(atr_prev).astype(float).to_numpy()


def used_fraction(pre, atr_prev):
    """Today's high-low as it stood at each bar's close, over yesterday's
    14-day daily ATR. NaN where the ATR is still warming up."""
    used = pre["used_today"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(atr_prev > 0, used / atr_prev, np.nan)


def exhaustion_gate(base, frac, limit):
    """base gate AND 'the day has not used more than `limit` of its ATR'.
    None = no veto. A NaN (ATR warm-up) never blocks."""
    def gate(i, r):
        if not base(i, r):
            return False
        if limit is None:
            return True
        f = frac[i]
        return not (f == f) or f <= limit
    return gate


def stats_of(rows):
    return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
            "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}


def evaluate(limit, frac_by_index, hists, F, A, pre_by_index, st_arr, target_key):
    gates = {k: exhaustion_gate(rs.make_gates(F[k])["opening-range break"], frac_by_index[k], limit)
             for k in INDICES}
    per_index = asg.build_candidates(hists, F, A, pre_by_index, st_arr, gates, target_key)
    kept = {}
    for k in INDICES:
        kept[k] = ccs.sequential_trades_conditional(
            per_index[k], base_cooldown_min=config.REENTRY_COOLDOWN_MIN,
            waive_adx_threshold=config.ADX_TREND_THRESHOLD)
    return stats_of([r for k in INDICES for r in kept[k]]), kept


def main():
    print("Loading history...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A, pre_by_index, st_arr, frac_by_index = {}, {}, {}, {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(),
                "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
        pre_by_index[k] = bt.precompute(df, k)
        st_len, st_mult = config.supertrend_params(k)
        st_line, _ = ind.supertrend(df, st_len, st_mult)
        st_arr[k] = st_line.to_numpy()
        frac_by_index[k] = used_fraction(pre_by_index[k], prev_day_atr(df))
    target_key = str(getattr(config, "EXIT_AT_TARGET", "T3") or "T3").lower()

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:40s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    print("=" * 134)
    print(" NIFTY + Bank Nifty + Sensex, real expiries, after costs - TRUE live exit "
          "(T1-ratchet + deployed Supertrend trail), deployed cooldown waiver")
    print("=" * 134)
    print(f" {'entry rule':40s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
          f"  | OUT-OF-SAMPLE (held-out final year)")
    print("-" * 134)

    args = (frac_by_index, hists, F, A, pre_by_index, st_arr, target_key)
    base, base_kept = evaluate(None, *args)
    print(line("today (no exhaustion veto)", base))
    results = {}
    for limit in (0.50, 0.70, 0.85, 1.00, 1.25):
        name = (f"no entry once day used > {limit:.0%} of ATR"
                + ("  (the paste)" if limit == 0.85 else ""))
        results[name], _ = evaluate(limit, *args)
        print(line(name, results[name]))

    print("\n VERDICT — kept only if better than today, BOTH periods")
    for name, r in results.items():
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        print(f"   {'KEEP ' if (di > 0 and do > 0) else 'drop '} {name:52s} in-sample {di:>+10,.0f}"
              f"   out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")

    # Today's own trades, by how much of the 14-day ATR the day had used at
    # entry. pro_study.price() stamps "when" at the signal bar's CLOSE (index +
    # 15 min), so the signal bar itself is when - 15 minutes.
    print("\n THE TRADES TODAY'S SYSTEM TAKES, BY HOW MUCH OF THE DAILY ATR WAS ALREADY USED AT ENTRY")
    print(f" {'bucket':30s} {'IN-SAMPLE: n, total, avg/trade':38s}  | HELD-OUT")
    rows = []
    for k in INDICES:
        pos = {t: n for n, t in enumerate(hists[k].index)}
        for r in base_kept[k]:
            i = pos[r["when"] - pd.Timedelta(minutes=15)]
            rows.append((r, frac_by_index[k][i]))
    def show(label, lo, hi):
        s = stats_of([r for r, f in rows if f == f and lo < f <= hi])
        a, b = s["is"], s["oos"]
        avg = lambda x: (x["total"] / x["n"]) if x["n"] else 0.0
        print(f" {label:30s} {a['n']:>5} ₹{a['total']:>+10,.0f}  ₹{avg(a):>+7,.0f}/trade"
              f"  | {b['n']:>4} ₹{b['total']:>+9,.0f}  ₹{avg(b):>+7,.0f}/trade")
    show("used 0-50% of ATR", -1, 0.50)
    show("used 50-85%", 0.50, 0.85)
    show("used 85-100% (paste: stop)", 0.85, 1.00)
    show("used 100-150%", 1.00, 1.50)
    show("used over 150%", 1.50, 1e9)
    nan_n = sum(1 for _, f in rows if not (f == f))
    print(f" ({nan_n} trades in the ATR warm-up days, unbucketed)")
    return {"base": base, **results}


if __name__ == "__main__":
    main()
