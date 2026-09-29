#!/usr/bin/env python3
"""
stalled_trend_filter_study.py — does the entry gate need to know a trend is stalled?
================================================================================
The user, 29 Sep 2026: "when the market trend says stalled going no where or momentum fading
... and even the things we see like trend, macd, rsi, vwap, pcr and adx gate matches call or put
it takes trades - does the tool look at the market trend explanation or just as soon as it
matches it takes the trade - which way is good can you check it."

WHAT THE CODE ACTUALLY DOES TODAY - THE TWO PATHS NEVER TALK
    "Market trend says stalled" comes from signal_engine.compute_market_trend() - a DIFFERENT,
    PARALLEL function from the one that decides CE/PE entries. Its own module docstring says so:
    "deliberately SEPARATE from the CE/PE signal." It checks ADX *and* how far price has actually
    moved (displacement, in ATR units, over the ADX window) - config.py's own comment explains
    why: "BankNifty labelled STRONG DOWNTREND with a day move of -7.55 points, or -0.01%. ADX is
    a lagging 14-bar average - a real move can finish, price can flatten, and ADX stays elevated
    for hours afterwards. The panel was even printing 'momentum fading' beside it, which is the
    same fact said quietly." That fix (TREND_MIN_DISPLACEMENT_ATR) was made for the DISPLAY LABEL.

    The entry decision - signal_engine.compute_technical_signal()'s "adx_ok", used by
    build_recommendation() - is a bare threshold: `adx_val >= config.ADX_TREND_THRESHOLD`. No
    displacement check. tickets.py's four real gates (_regime_hold, _divergence_hold,
    _reward_hold, _spread_hold) don't check it either - confirmed by reading all four. So: yes,
    exactly what the user noticed happens. Trend/MACD/RSI/VWAP agreeing plus a bare ADX>=20 can
    fire a trade at the same moment the trend panel is independently saying "STALLED - GOING
    NOWHERE" or "momentum fading", because the fix for that exact wording was never carried over
    to the code path that risks money.

WHAT THIS TESTS
    Whether it SHOULD be carried over: the same "stalled" definition compute_market_trend() uses
    (ADX at/above the trend threshold, but net price displacement over the ADX window under
    TREND_MIN_DISPLACEMENT_ATR) as an ADDITIONAL veto on top of today's real entry gates. Same
    3-year real NIFTY/BANKNIFTY/SENSEX history, the same real gates (reversal_exit_study.live_
    gate, not a re-approximation), the same live exit (hold to T2/stop), the same real-expiry
    pricing and after-costs stats(). Kept only if it beats the baseline both in-sample and on the
    held-out final year - this project's own standing bar.

    29 Sep 2026, the user: "adjust the min displacement threshold and retest" - at the live
    default (1.0 ATR) this was a close DROP (won in-sample, lost a little held-out). This now
    sweeps a small, pre-declared range of thresholds around it (0.5-2.0) rather than hand-picking
    one number after seeing a result - pro_study.py's own module docstring says why: "trying ten
    settings of each and keeping the best is how a backtest learns the past instead of the
    market." Every threshold's numbers are printed, not just the best one, and a threshold is
    trusted only if it clears the same both-periods bar - a table with one lucky row is not
    evidence that row is real.

    python3 stalled_trend_filter_study.py
"""
import math

import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import indicators as ind
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = ps.INDICES
SPLIT = ps.SPLIT


def stalled_series(df, index_key, threshold=None):
    """The exact formula signal_engine.compute_market_trend() uses, vectorised over the whole
    series - NOT regime_study.features()'s 'stalled' column, which leaves out the per-market DX
    smoothing (config.ADX_DX_SMOOTHING) compute_market_trend()'s own ADX call passes. That gap is
    real (confirmed by cross-checking regime_study's column against compute_market_trend() itself
    on real NIFTY history in the test - it does not always agree), so this recomputes it directly
    rather than reuse a column that is close but not the same thing the trend panel prints. ADX
    and ATR are both causal (backward-looking only), so a bar's value here is identical to what
    compute_market_trend() would compute if called with history truncated to that bar.

    `threshold` overrides config.TREND_MIN_DISPLACEMENT_ATR (1.0, today's live value) - RAISING it
    calls MORE bars stalled (a smaller net move now counts as "hasn't gone anywhere"), LOWERING it
    calls fewer."""
    th = config.TREND_MIN_DISPLACEMENT_ATR if threshold is None else threshold
    close = df["Close"]
    adx = ind.adx(df, config.ADX_LENGTH, config.adx_dx_smoothing(index_key))
    atr = ind.atr(df, config.ATR_LENGTH)
    n = config.ADX_LENGTH
    displacement = (close - close.shift(n)).abs() / atr
    strong_or_moderate = adx >= config.ADX_TREND_THRESHOLD
    return (strong_or_moderate & (displacement < th)).to_numpy()


def stalled_gate(index_key, i, rec, df, stalled_arr):
    """Today's real entry gates, PLUS: the entry bar is not one compute_market_trend() would
    call stalled - ADX at/above the trend threshold with price not actually having moved."""
    if not res.live_gate(index_key, i, rec, df):
        return False
    return not bool(stalled_arr[i])


def main():
    print("Loading history (shared cache with pro_study.py / backtest_intraday.py)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}

    base_gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    def run_one(gate_map):
        rows = []
        n_raw = 0
        for k in INDICES:
            df = hists[k]
            result = bt.run(k, df, gate=gate_map[k])
            n_raw += len(result["trades"])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in result["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = ps.simulate(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}, n_raw

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    # Pre-declared before any of them are run, not picked after seeing a result. 1.0 is today's
    # live TREND_MIN_DISPLACEMENT_ATR - kept in the sweep as the reference row, since it is the
    # one already reported once (29 Sep 2026: close DROP, +57,604 in-sample, -13,042 held-out).
    THRESHOLDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print("=" * 132)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(" Entries: today's real gates (unchanged). Exit: hold to T2/stop (live, today), unchanged.")
    print(" Only variable: the ATR displacement threshold below which a strong-ADX bar is vetoed")
    print(" as 'stalled' - TREND_MIN_DISPLACEMENT_ATR is 1.0 live. Higher = stricter (vetoes more).")
    print("=" * 132)
    print(hdr)
    print("-" * 132)

    base, base_raw = run_one(base_gate)
    print(line("today's live gate (baseline)", base))
    print("-" * 132)

    results = {}
    for th in THRESHOLDS:
        stalled_arrs = {k: stalled_series(hists[k], k, threshold=th) for k in INDICES}
        filt_gate = {k: (lambda i, rec, k=k, sa=stalled_arrs: stalled_gate(k, i, rec, hists[k], sa[k]))
                     for k in INDICES}
        r, raw = run_one(filt_gate)
        results[th] = (r, raw)
        kept_pct = 100 * raw / base_raw if base_raw else 0.0
        live = "  (today's live value)" if th == config.TREND_MIN_DISPLACEMENT_ATR else ""
        print(line(f"veto below {th:.2f} ATR" + live, r) + f"   (kept {kept_pct:4.1f}%)")
    print("-" * 132)

    print("\n VERDICT — kept only if better than the baseline both in-sample and out-of-sample,")
    print(" at EVERY threshold reported, not just whichever one happens to win here")
    kept_any = False
    for th in THRESHOLDS:
        r, raw = results[th]
        di = r["is"]["total"] - base["is"]["total"]
        do = r["oos"]["total"] - base["oos"]["total"]
        dd = r["oos"]["dd"] - base["oos"]["dd"]
        keep = di > 0 and do > 0
        kept_any = kept_any or keep
        print(f"   {'KEEP ' if keep else 'drop '} {th:.2f} ATR   in-sample {di:>+10,.0f}   "
              f"out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    print(f"\n   {'At least one threshold clears the bar.' if kept_any else 'No threshold in this range clears the bar both periods.'}")
    if kept_any:
        print("   A single winning threshold in a swept range this narrow is exactly the kind of")
        print("   result pro_study.py's own docstring warns about - it is evidence worth a second")
        print("   look (a held-out year beyond this one, or a live paper run), not adoption on its own.")
    return {"base": base, "results": results}


if __name__ == "__main__":
    main()
