#!/usr/bin/env python3
"""
adx_rising_vote_study.py — "ADX rising" as a vote beside the others, against today's live rules (3 years)
================================================================================
The user, 13 Oct 2026: "i want to check if we add adx rising to current rules what will be the result adding this
vote with others".

Today's rules (Sensex and Midcap Select, config.SYSTEM_DEFAULTS) vote Trend, MACD, RSI and VWAP; "strict" needs 3 to
agree with at most 1 against, then ADX at least 20 and MACD agreeing. ADX says how strong a trend is, not which way,
so as a vote it needs a direction. PRE-DECLARED BEFORE ANY RESULT, ADX(14) as live, "rising" = above its last
candle's, NOT SWEPT:
    A  "ADX rising"          while ADX rises it votes the +DI/-DI side (+DI over -DI up, else down); else abstains
    B  "ADX rising (lean)"   while ADX rises it votes with whichever way the other four lean (the trend strengthening);
                             else abstains - it can make 2 agreeing votes enough, never a lean on its own
  Each ADDED as a fifth vote through config.VOTE_OVERRIDES (as bollinger_vote_study.py added Bollinger), so the live
  agreement rule decides with it; nothing else touched. signal_engine and backtest_intraday compute both identically
  (backtest_intraday.verify_precompute checks the two).
  For comparison, not a choice: C - ADX rising as a REQUIREMENT on every entry (the filter stop_day_study.py dropped
  on 7 Oct), on this harness.

EVERYTHING ELSE AS bollinger_vote_study.py: the live entry checks and exit, one position per index with the cooldown
waiver, per lot after Zerodha's costs, real expiries, 0.25% slippage a side, Midcap monthly; "today" asserted to
reproduce its figures to the rupee. (The T1-exit re-entry waiver deployed this afternoon is not in this harness's
"today" - it changes when a re-entry may come, not which candles vote.) KEPT only if better than today in BOTH the
first two years and the held-out final year, Sensex + Midcap pooled.

RESULT (13 Oct 2026, history to 11 Sep 2026, per lot after costs; adx_rising_vote_study_results.txt) - ALL DROP.
  Sensex + Midcap, today +252,225 / +186,668 (first two years / held-out):
    A  + ADX rising, +DI/-DI side      -909 / -19,291    held-out worst drop 18,144 deeper
    B  + ADX rising, with the lean   +1,444 / -12,416    held-out worst drop 15,597 deeper
    C  ADX rising required          +26,808 / -46,329    (Midcap better both periods, Sensex -17,610 / -50,385)
  Vote A agrees with the EMA Trend vote on 90.4% of candles where both vote - a second vote for what Trend says, as
  +DI/-DI (85%) and Bollinger (79-92%) were. Picking an index or a version after seeing this would be choosing a winner
  after the fact. Nothing changed live: both votes stay available behind config.VOTE_OVERRIDES, off.

    python3 adx_rising_vote_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
LIVE_RULES = ("SENSEX", "MIDCPNIFTY")
NAMES = {"SENSEX": "Sensex", "MIDCPNIFTY": "Midcap Select"}
VARIANTS = (("today", None, "today (Trend / MACD / RSI / VWAP)"),
            ("A", "ADX rising", "A: + ADX rising, the +DI/-DI side"),
            ("B", "ADX rising (lean)", "B: + ADX rising, with the others' lean"),
            ("C", None, "(compare) C: ADX rising REQUIRED on every entry"))


def _job(job):
    key, variant = job
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indicators as ind
    import opening_window_study as ows
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    add = {v: a for v, a, _ in VARIANTS}[variant]
    config.VOTE_OVERRIDES = dict(config.VOTE_OVERRIDES, nse_index=({"add": [add]} if add else {}))
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    F = rs.features(df, vix, nrv)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(), "days": set(df.index.date)}
    pre = bt.precompute(df, key)
    st = ind.supertrend(df, *config.supertrend_params(key))[0].to_numpy()
    target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
    adx = pre["adx"].to_numpy()
    if variant == "C":
        gate = lambda i, rec: i > 0 and adx[i] > adx[i - 1] and res.live_gate(key, i, rec, df)
    else:
        gate = lambda i, rec: res.live_gate(key, i, rec, df)
    rows = ows.candidates(key, df, gate, A, F, pre, st, target, True)
    # how often vote A agrees with the EMA Trend vote, where both vote (+DI/-DI was 85%, Bollinger 79-92%)
    agree = both = 0
    if variant == "A":
        for i in range(200, len(df)):
            t = bt.tech_at(df, pre, i)
            if t["adx_rise_score"] and t["trend_score"]:
                both += 1
                agree += t["adx_rise_score"] == t["trend_score"]
    return job, (rows, (agree, both))


def main():
    from concurrent.futures import ProcessPoolExecutor
    import bollinger_vote_study as bvs
    import stop_day_study as sds
    jobs = [(k, v) for k in LIVE_RULES for v, _, _ in VARIANTS]
    print("Replaying the live rules with ADX rising as a vote...", flush=True)
    with ProcessPoolExecutor(max_workers=6) as ex:
        got = dict(ex.map(_job, jobs))
    for k in LIVE_RULES:
        a, b = bvs.BASELINE[k]
        s = sds.split(got[(k, "today")][0])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), f"{k}: today must reproduce the baseline"
    W = 172
    print("=" * W)
    print(" ADX RISING AS A FIFTH VOTE - per lot, after costs, the live entry checks and exit.  columns: trades, won, total,"
          " profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    pooled = lambda v: sorted([r for k in LIVE_RULES for r in got[(k, v)][0]], key=lambda r: r["when"])
    print(" SENSEX + MIDCAP SELECT (the verdict)")
    for v, _, label in VARIANTS:
        print(sds.line("   " + label, pooled(v)))
    for k in LIVE_RULES:
        print("-" * W)
        print(f" {NAMES[k]}")
        for v, _, label in VARIANTS:
            print(sds.line("   " + label, got[(k, v)][0]))
    print("\n VERDICT (Sensex + Midcap Select) - kept only if better than today in BOTH periods")
    for v, _, label in VARIANTS[1:]:
        print("  " + sds.verdict(label, pooled(v), pooled("today")).strip())
    a = sum(got[(k, "A")][1][0] for k in LIVE_RULES)
    b = sum(got[(k, "A")][1][1] for k in LIVE_RULES)
    print(f"\n Vote A agrees with the EMA Trend vote on {100 * a / max(1, b):.1f}% of the candles where both voted ({b:,})"
          " - +DI/-DI was 85%, Bollinger 79-92%")
    return got


if __name__ == "__main__":
    main()
