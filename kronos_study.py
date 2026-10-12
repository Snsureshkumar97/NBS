#!/usr/bin/env python3
"""
kronos_study.py — does Kronos forecast Indian index moves, and does it improve the tool?
================================================================================
The user, 12 Oct 2026: "yes test kronos on our indian data". Kronos (Shi et al., arXiv 2508.02739, AAAI 2026) is an
open-source foundation model for candlestick data; kronos_forecast.py ran its Kronos-small model over each index's
3-year 15-minute history (in its own environment, ~/kronos-test - the tool never imports it) and saved, at every
candle where the tool's engine signals and the live entry checks pass, the closes it forecast for the next hour.
This reads those forecasts. FIXED BEFORE ANY RESULT WAS SEEN:

  1. SKILL - at those candles, the ones whose next hour stays inside the same session (signal candle 14:15 or
     earlier): how often the forecast's direction (its close 4 candles ahead vs the close now) matches what the index
     actually did, against three baselines at the same candles: the tool's own signal direction (a call = up), "same
     as the last hour", and "always up". Hit rate with a 95% margin, each period.
  2. FILTER - the live system (bollinger_vote_study.py's harness: the live entry checks and exit, one position per
     index with the cooldown waiver, per lot after costs, real expiries, 0.25% slippage, Midcap monthly) taking a
     trade only when Kronos's one-hour forecast agrees with it (a call needs a forecast close above the close now, a
     put below). A candle Kronos has no forecast for (the first 400 of the history) passes unchanged, so every
     difference is Kronos's own call. The 15-minute forecast's agreement is shown for robustness only.

THE MODEL MAY HAVE TRAINED ON 2023-2025 INDIAN CANDLES (its weights were made in mid-2025), so the first two years can
flatter it. The held-out final year (from 15 Aug 2025) is after the model was made: the verdict rests on it - KEPT
only if the filter is better there AND not worse in the first two years, on Sensex + Midcap Select (where the tool's
own rules take entries live; Nifty and Bank Nifty run the Trend Rider and are shown for information).

    python3 kronos_study.py

RESULT (12 Oct 2026, history to 11 Sep 2026; full table in kronos_study_results.txt) - DROP, clearly.
  1. No skill: held-out direction right 49.5-51.2% on the four indices, every one inside its ~2-point margin of a coin;
     the first two years 47.8-50.1%. The tool's own signal direction does as well or better on three of four. Kronos
     agrees with the tool's direction only 31-38% of the time - it leans toward a pullback where the tool sees a trend.
  2. As a filter it removes about a third of the trades and most of the profit. Sensex + Midcap Select, today
     +252,225 / +186,668: one-hour filter +157,339 / +24,332 (-94,886 / -162,336), worst drop deeper both periods.
     The 15-minute forecast: +27,038 / +70,820 - also far worse. All four indices: +579,454 / +211,678 -> +342,043 /
     +11,307. On Nifty and Sensex the first two years looked BETTER (Nifty PF 1.30 -> 1.50, worst drop halved) and
     the held-out year did not hold it - the pattern of a period the model may have trained on, or of chance.
"""
import math
import os

import numpy as np
import pandas as pd

import bollinger_vote_study as bvs

OUT = os.path.expanduser("~/kronos-test/out")
INDICES = bvs.INDICES
LIVE_RULES = bvs.LIVE_RULES
NAMES = bvs.NAMES
VARIANTS = (("today", None, "today (no Kronos)"),
            ("k1h", "f4", "+ Kronos agrees: one-hour forecast"),
            ("k15", "f1", "  + Kronos agrees: 15-minute forecast (robustness)"))


def forecasts(key):
    """{candle number: {"close", "f1".."f4"}} from kronos_forecast.py's output."""
    df = pd.read_csv(os.path.join(OUT, f"{key}.csv")).drop_duplicates("i", keep="last")
    return {int(r.i): {"close": r.close, "f1": r.f1, "f2": r.f2, "f3": r.f3, "f4": r.f4} for r in df.itertuples()}


def agrees(fc, col, side):
    """Kronos's forecast agrees with a trade: a call needs the forecast close above the close now, a put below.
    No forecast for this candle: None (the caller lets it pass)."""
    if fc is None:
        return None
    move = fc[col] - fc["close"]
    return move > 0 if side == "CE" else move < 0


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
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    col = dict((v, c) for v, c, _ in VARIANTS)[variant]
    fcs = forecasts(key) if col else {}
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

    def gate(i, rec):
        if not res.live_gate(key, i, rec, df):
            return False
        if not col:
            return True
        ok = agrees(fcs.get(i), col, rec["option_type"])
        return True if ok is None else ok

    rows = ows.candidates(key, df, gate, A, F, pre, st, target, True)
    return job, rows


def skill(key):
    """Per period: (n, Kronos hit %, signal hit %, last-hour hit %, always-up hit %) at the candidate candles whose
    next hour stays inside the session, and how often Kronos agreed with the signal."""
    import backtest_intraday as bt
    import reversal_exit_study as res
    df = bt.fetch_history(key, years=3, use_cache=True)
    side = {}

    def rec_gate(i, rec):
        if res.live_gate(key, i, rec, df):
            side[i] = rec["option_type"]
        return False
    bt.run(key, df, gate=rec_gate)
    fcs = forecasts(key)
    cl = df["Close"].to_numpy()
    times = df.index
    out = {}
    for period, sel in (("is", lambda t: t < bvs_split()), ("oos", lambda t: t >= bvs_split())):
        rows = []
        for i, s in side.items():
            f = fcs.get(i)
            if f is None or i < 4 or i + 4 >= len(cl) or not sel(times[i]):
                continue
            if times[i + 4].date() != times[i].date():
                continue                                  # the next hour must stay inside this session
            actual = cl[i + 4] - cl[i]
            if actual == 0:
                continue
            up = actual > 0
            rows.append((f["f4"] > f["close"], s == "CE", cl[i] > cl[i - 4], up))
        if not rows:
            out[period] = None
            continue
        a = np.array(rows)
        n = len(a)
        hit = lambda col: float((a[:, col] == a[:, 3]).mean() * 100)
        out[period] = {"n": n, "kronos": hit(0), "signal": hit(1), "last_hour": hit(2),
                       "always_up": float(a[:, 3].mean() * 100), "agree": float((a[:, 0] == a[:, 1]).mean() * 100),
                       "margin": 1.96 * math.sqrt(0.25 / n) * 100}
    return out


def bvs_split():
    import regime_study as rs
    return rs.SPLIT


def main():
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    for k in INDICES:
        assert os.path.exists(os.path.join(OUT, f"{k}.csv")), f"run kronos_forecast.py --index {k} first"

    print("=" * 140)
    print(" 1. SKILL - at the candles where the tool signals and its entry checks pass, next hour inside the session.")
    print("    Direction right, % (a coin is 50; the 95% margin is shown). Kronos vs the tool's own signal direction,")
    print("    'same as the last hour', and 'always up'.")
    print("=" * 140)
    sk = {k: skill(k) for k in INDICES}
    for p, title in (("is", "FIRST TWO YEARS (the model may have trained on these)"),
                     ("oos", "HELD-OUT FINAL YEAR (after the model was made)")):
        print(f" {title}")
        print(f"   {'':15s} {'candles':>8} {'Kronos':>9} {'signal':>8} {'last hour':>10} {'always up':>10}"
              f" {'margin':>8} {'Kronos agrees w/ signal':>24}")
        for k in INDICES:
            r = sk[k][p]
            if r:
                print(f"   {NAMES[k]:15s} {r['n']:>8,} {r['kronos']:>8.1f}% {r['signal']:>7.1f}% {r['last_hour']:>9.1f}%"
                      f" {r['always_up']:>9.1f}%  ±{r['margin']:.1f}  {r['agree']:>22.1f}%")

    jobs = [(k, v) for k in INDICES for v, _, _ in VARIANTS]
    print(f"\nReplaying the live system: {len(INDICES)} indices x {len(VARIANTS)} entry rules, side by side...", flush=True)
    with ProcessPoolExecutor(max_workers=6) as ex:
        got = dict(ex.map(_job, jobs))
    for k, (a, b) in bvs.BASELINE.items():
        s = sds.split(got[(k, "today")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: 'today' must reproduce index_candidates_study.py's figures, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"

    print("=" * 172)
    print(" 2. FILTER - trade only when Kronos's forecast agrees. Per lot, after costs.  columns: trades, won, total, profit"
          " factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    for k in INDICES:
        print("-" * 172)
        tag = "  <- the tool's own rules take entries here live" if k in LIVE_RULES else "  (runs the Trend Rider live - for information)"
        print(f" {NAMES[k]}{tag}")
        for v, _, label in VARIANTS:
            print(sds.line("   " + label, got[(k, v)]))

    def pooled(keys, v):
        return [r for k in keys for r in got[(k, v)]]
    for keys, title in ((LIVE_RULES, "SENSEX + MIDCAP SELECT - the verdict"),
                        (tuple(INDICES), "ALL FOUR UNDER THE TOOL'S RULES - for information")):
        print("\n" + "=" * 172)
        print(f" {title}")
        print("=" * 172)
        for v, _, label in VARIANTS:
            print(sds.line("   " + label, pooled(keys, v)))

    base = sds.split(pooled(LIVE_RULES, "today"))
    r = sds.split(pooled(LIVE_RULES, "k1h"))
    di, do = r["is"]["total"] - base["is"]["total"], r["oos"]["total"] - base["oos"]["total"]
    keep = do > 0 and di >= 0
    print("\n VERDICT (Sensex + Midcap Select) - kept only if better in the held-out year AND not worse in the first two")
    print(f"   {'KEEP' if keep else 'drop'}  Kronos one-hour filter   first two years {di:>+10,.0f}   held-out {do:>+10,.0f}"
          f"   worst drop: {r['is']['dd'] - base['is']['dd']:>+9,.0f} / {r['oos']['dd'] - base['oos']['dd']:>+9,.0f}"
          f"   trades {r['is']['n'] - base['is']['n']:>+5} / {r['oos']['n'] - base['oos']['n']:>+5}")
    return sk, got


if __name__ == "__main__":
    main()
