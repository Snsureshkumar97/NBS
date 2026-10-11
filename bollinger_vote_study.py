#!/usr/bin/env python3
"""
bollinger_vote_study.py — Bollinger Bands as a vote in the Indian market, against today's live rules
================================================================================
The user, 11 Oct 2026: "did we check the results adding bollinger bands also in the votes in the indian market".
Not before: Bollinger was tested twice in September as a FILTER on trades already taken (strategy_study.py's
"break"/"reversion" entry conditions, 13 Sep; pro_study.py's "don't chase", 15 Sep - both dropped), never as a VOTE
beside Trend, MACD, RSI and VWAP. Then "yes" to testing it.

TWO READINGS, PRE-DECLARED BEFORE ANY RESULT, TEXTBOOK SETTINGS (20 candles, 2 standard deviations, population
deviation as TradingView draws it), NOT SWEPT:
    "Bollinger"        price above the bands' middle (the 20-candle average) votes +1, below votes -1
    "Bollinger break"  a close above the upper band votes +1, below the lower band -1, inside the bands abstains
Each is ADDED as a fifth vote through config.VOTE_OVERRIDES (the switch crypto's vote set uses), so the live
agreement rule decides with it exactly as it would live - no other rule touched (MACD must agree, the ADX gate,
the holds).

EVERYTHING ELSE AS opening_window_study.py (read its header): the live entry checks (reversal_exit_study.live_gate,
tickets.py's own holds), the live exit (stop, T1 ratchet, Supertrend trail, out at T2, 2-hour breakeven priced as it
fills, day-end), one position per index with the cooldown waiver, per lot after Zerodha costs, real expiries, 0.25%
slippage a side. Midcap Select on its monthly expiries, as index_candidates_study.py priced it (its options are
monthly only).

WHERE IT WOULD APPLY: the votes decide entries only on the indices that run the tool's own rules - Sensex and Midcap
Select today (config.SYSTEM_DEFAULTS). Nifty and Bank Nifty run the Trend Rider, which has no votes. So the verdict is
judged on Sensex + Midcap; Nifty and Bank Nifty are shown under the rules engine for information only.
KEPT only if better than today in BOTH periods (in-sample to 15 Aug 2025, then the held-out final year).

Harness check: "today" must reproduce index_candidates_study.py's figures for all four indices to the rupee.

RESULT (11 Oct 2026, history to 11 Sep 2026, per lot after costs; full table in bollinger_vote_study_results.txt) -
BOTH DROP. Sensex + Midcap Select, today +252,225 / +186,668:
    + Bollinger (middle band)   -24,827 in-sample   -24,587 held-out   held-out worst drop 16,382 deeper
    + Bollinger break           -6,645 in-sample    -33,236 held-out   held-out worst drop 21,367 deeper
Per index it is mixed: Sensex alone gains a little with the middle band in both periods (+7,293 / +6,011), Midcap
Select loses a lot (-32,121 / -30,598); choosing an index after seeing this would be picking a winner after the fact.
Why so little: where both vote, the middle band agrees with the EMA Trend vote ~79% of candles and the band break ~92%
- mostly a second vote for what Trend already says (as +DI/-DI's 85% was). Nothing changed live; the two votes stay
available behind config.VOTE_OVERRIDES, off.

    python3 bollinger_vote_study.py
"""
import math

import numpy as np
import pandas as pd

INDICES = ["SENSEX", "MIDCPNIFTY", "NIFTY", "BANKNIFTY"]
LIVE_RULES = ("SENSEX", "MIDCPNIFTY")          # config.SYSTEM_DEFAULTS: the indices the votes decide
VARIANTS = (("today", None, "today (Trend / MACD / RSI / VWAP)"),
            ("mid", "Bollinger", "+ Bollinger: above / below the middle band"),
            ("break", "Bollinger break", "+ Bollinger break: outside a band"))
NAMES = {"SENSEX": "Sensex", "MIDCPNIFTY": "Midcap Select", "NIFTY": "Nifty", "BANKNIFTY": "Bank Nifty"}
# today's figures for each index (index_candidates_study.py, 7 Oct 2026): in-sample, held-out
BASELINE = {"NIFTY": (154936, 30386), "SENSEX": (110280, 100217), "BANKNIFTY": (172293, -5376),
            "MIDCPNIFTY": (141945, 86451)}


def _job(job):
    key, variant = job
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import indicators as ind
    import opening_window_study as ows
    import pro_study as ps
    import regime_study as rs
    import reversal_exit_study as res
    ps.SLIP = 0.0025
    if key == "MIDCPNIFTY":                     # monthly options only, as index_candidates_study.py priced them
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    add = dict(VARIANTS_ADD)[variant]
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
    rows = ows.candidates(key, df, lambda i, rec: res.live_gate(key, i, rec, df), A, F, pre, st, target, True)
    return job, rows


VARIANTS_ADD = [(v, add) for v, add, _ in VARIANTS]


def main():
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    jobs = [(k, v) for k in INDICES for v, _, _ in VARIANTS]
    print(f"Replaying the live system: {len(INDICES)} indices x {len(VARIANTS)} vote sets, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=6) as ex:
        got = dict(ex.map(_job, jobs))
    for k, (a, b) in BASELINE.items():
        s = sds.split(got[(k, "today")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: 'today' must reproduce index_candidates_study.py's figures, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"

    print("=" * 172)
    print(" BOLLINGER (20, 2) AS A FIFTH VOTE - per lot, after costs, the live entry checks and exit.  columns: trades, won,"
          " total, profit factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'IN-SAMPLE (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for k in INDICES:
        print("-" * 172)
        tag = "  <- the votes decide entries here live" if k in LIVE_RULES else "  (runs the Trend Rider live - for information)"
        print(f" {NAMES[k]}{tag}")
        for v, _, label in VARIANTS:
            print(sds.line("   " + label, got[(k, v)]))

    def pooled(keys, v):
        return [r for k in keys for r in got[(k, v)]]

    for keys, title in ((LIVE_RULES, "SENSEX + MIDCAP SELECT - where the votes decide entries live (the verdict)"),
                        (tuple(INDICES), "ALL FOUR UNDER THE TOOL'S RULES - for information")):
        print("\n" + "=" * 172)
        print(f" {title}")
        print("=" * 172)
        for v, _, label in VARIANTS:
            print(sds.line("   " + label, pooled(keys, v)))

    base = sds.split(pooled(LIVE_RULES, "today"))
    print("\n VERDICT (Sensex + Midcap Select) - kept only if better than today in BOTH periods")
    for v, _, label in VARIANTS[1:]:
        r = sds.split(pooled(LIVE_RULES, v))
        di, do = r["is"]["total"] - base["is"]["total"], r["oos"]["total"] - base["oos"]["total"]
        print(f"   {'KEEP' if di > 0 and do > 0 else 'drop'}  {label:44s} in-sample {di:>+10,.0f}  held-out {do:>+10,.0f}"
              f"   worst drop: in-sample {r['is']['dd'] - base['is']['dd']:>+9,.0f}  held-out"
              f" {r['oos']['dd'] - base['oos']['dd']:>+9,.0f}   trades {r['is']['n'] - base['is']['n']:>+5} /"
              f" {r['oos']['n'] - base['oos']['n']:>+5}")

    # The +DI/-DI study's lesson (2 Oct 2026): a vote that says what Trend already says adds nothing. How often each
    # Bollinger reading agrees with the EMA Trend vote, over every candle where both had an opinion.
    import backtest_intraday as bt
    print("\n AGREEMENT WITH THE EMA TREND VOTE, every candle where both voted (+DI/-DI was 85% - mostly a re-vote)")
    for k in INDICES:
        df = bt.fetch_history(k, years=3, use_cache=True)
        pre = bt.precompute(df, k)
        c, ef, es = df["Close"].to_numpy(), pre["ema_fast"].to_numpy(), pre["ema_slow"].to_numpy()
        trend = np.where((c > es) & (ef > es), 1, np.where((c < es) & (ef < es), -1, 0))
        mid = np.where(c > pre["bb_mid"].to_numpy(), 1, np.where(c < pre["bb_mid"].to_numpy(), -1, 0))
        brk = np.where(c > pre["bb_up"].to_numpy(), 1, np.where(c < pre["bb_lo"].to_numpy(), -1, 0))
        cells = []
        for name, v in (("middle band", mid), ("band break", brk)):
            both = (trend != 0) & (v != 0)
            cells.append(f"{name}: {100 * (trend[both] == v[both]).mean():4.1f}% of {both.sum():,} candles")
        print(f"   {NAMES[k]:14s} " + "   ".join(cells))
    return got


if __name__ == "__main__":
    main()
