#!/usr/bin/env python3
"""
time_breakeven_study.py — tighten the stop to breakeven if a trade goes nowhere, not close it
================================================================================
The user, 29 Sep 2026: "look for a new exit or sizing idea" (after three entry-side filters -
candlestick shape, Gann/volume, stalled-trend - all failed to generalize the same way).

SURVEY OF WHAT'S ALREADY BEEN TRIED, SO THIS ISN'T A RETREAD
    Exit side: T1/T2/T3 ladder choice (T2 live - rule_review.py, 11 Sep 2026, found it at least as
    good as T3 with a quarter less drawdown; target_ladder_study.py's newer comparison is not
    itself a recommendation to switch and this does not revisit that question). EARLY_EXIT_ON_
    REVERSAL (adopted). Breakeven-after-T1, half-off-at-T1, a fixed two-hour time stop that CLOSES
    the trade (all pro_study.py, dropped). An ATR trailing stop after T1 (skills_study.py,
    "worse in both"). Sizing side: a drawdown circuit breaker and half-size-on-high-VIX
    (drawdown_controls_study.py, both dropped - worse profit-per-drawdown in-sample despite one
    helping the held-out year alone). Reward:risk threshold 0.7x vs 1x (open_and_rr_study.py,
    dropped). One-ticket-per-correlated-direction across indices (correlation_study.py, dropped).

WHAT'S GENUINELY DIFFERENT HERE
    pro_study.simulate()'s be_after_t1 moves the stop to breakeven once T1 is TOUCHED - a trade
    that IS working. That was dropped. This is the untested other half: move the stop to
    breakeven after WAIT_BARS have passed with T1 still NOT touched - a trade that ISN'T working
    yet, without giving up on it outright the way the (also dropped) fixed time_stop does, which
    force-closes at the current price instead. It only ever tightens the stop, never loosens it,
    and never closes the trade by itself - target, the real stop, and square-off all still apply
    exactly as they do live.

    This is motivated by the same diagnostic today's stalled-trend investigation produced: a
    setup that "hasn't gone anywhere" isn't reliably bad enough to refuse at ENTRY (vetoing it
    lost money out-of-sample), but a position that already went nowhere for a while might still
    be worth giving less room to on the downside, without discarding its upside the way an entry
    veto does. Different intervention point, tested on its own footing - not assumed to work
    because the diagnosis was interesting.

    WAIT_BARS = 8 (2 hours of 15-minute candles - the same "8 bars = 2 hours" convention
    flow_study.py already uses in this project), pre-declared, one setting, not swept.

METHODOLOGY - SAME ENTRIES, ONLY THE EXIT DIFFERS
    Today's real entry gates (reversal_exit_study.live_gate), computed once and re-simulated
    under both exits - so any difference between the two rows is the exit rule alone, exactly
    reversal_exit_study.py's own approach. Real-expiry pricing, after-costs stats(), the project's
    standing bar: kept only if it beats today's live exit both in-sample and out-of-sample.

    python3 time_breakeven_study.py
"""
import numpy as np
import pandas as pd

import backtest_intraday as bt
import config
import pro_study as ps
import regime_study as rs
import reversal_exit_study as res

INDICES = ps.INDICES
SPLIT = ps.SPLIT
WAIT_BARS = 8   # pre-declared: 2 hours of 15-minute candles


def simulate_time_breakeven(A, i, tr, hold_bars=26, square_off=True, target=None, wait_bars=WAIT_BARS):
    """Same target/stop/square-off checks as pro_study.simulate()'s default (live) exit, in the
    same order (stop first - the pessimistic reading, since a bar does not say which came first
    within it) - PLUS: once `wait_bars` have passed since entry with T1 still not touched, the
    stop tightens to breakeven (the entry price) for the rest of the hold. It only ever tightens
    (max for a CE, min for a PE - never moves the stop further away) and never closes the trade
    by itself; target and square-off are unaffected."""
    if target is None:
        target = str(getattr(config, "EXIT_AT_TARGET", "T2") or "T2").lower()
        if target not in ("t1", "t2", "t3"):
            target = "t2"
    hi, lo, cl, end = A["hi"], A["lo"], A["cl"], A["end"]
    ce = tr["side"] == "CE"
    stop, legs, rem, t1_done = tr["stop"], [], 1.0, False
    n = len(cl)
    for j in range(i + 1, min(i + 1 + hold_bars, n)):
        if not t1_done and (j - i) >= wait_bars:
            stop = max(stop, tr["entry"]) if ce else min(stop, tr["entry"])
        if (lo[j] <= stop) if ce else (hi[j] >= stop):
            legs.append((stop, j, rem))
            return legs
        if not t1_done and ((hi[j] >= tr["t1"]) if ce else (lo[j] <= tr["t1"])):
            t1_done = True
        tgt = tr.get(target)
        if tgt is not None and ((hi[j] >= tgt) if ce else (lo[j] <= tgt)):
            legs.append((tgt, j, rem))
            return legs
        if square_off and end[j]:
            legs.append((cl[j], j, rem))
            return legs
    last = min(i + hold_bars, n - 1)
    legs.append((cl[last], last, rem))
    return legs


def main():
    print("Loading history (shared cache with pro_study.py / backtest_intraday.py)...")
    hists = {k: bt.fetch_history(k, years=3, use_cache=True) for k in INDICES}
    vix = rs.load_vix()
    import math
    nrv = np.log(rs.daily_close(hists["NIFTY"])).diff().rolling(20).std() * math.sqrt(252)
    F = {k: rs.features(hists[k], vix, nrv) for k in INDICES}
    A = {}
    for k, df in hists.items():
        A[k] = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
                "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy(),
                "days": set(df.index.date)}
    base_gate = {k: (lambda i, rec, k=k: res.live_gate(k, i, rec, hists[k])) for k in INDICES}

    def run_one(exit_fn):
        rows = []
        for k in INDICES:
            df = hists[k]
            result = bt.run(k, df, gate=base_gate[k])
            pos = {t: n for n, t in enumerate(df.index)}
            sig = F[k]["sigma"].to_numpy()
            for tr in result["trades"]:
                i = pos[tr["when"]]
                s = sig[i]
                if not (s == s) or s <= 0:
                    continue
                legs = exit_fn(A[k], i, tr)
                p = ps.price(k, df, A[k], i, tr, legs, s)
                if p:
                    rows.append(p)
        return {"is": ps.stats([r for r in rows if r["when"] < SPLIT]),
                "oos": ps.stats([r for r in rows if r["when"] >= SPLIT])}

    def line(name, r):
        a, b = r["is"], r["oos"]
        return (f" {name:34s} {a['n']:>5} ₹{a['total']:>+10,.0f} PF {a['pf']:4.2f} DD ₹{a['dd']:>8,.0f}"
                f"  | {b['n']:>4} ₹{b['total']:>+10,.0f} PF {b['pf']:4.2f} DD ₹{b['dd']:>8,.0f}")

    hdr = (f" {'variant':34s} {'IN-SAMPLE: trades, total, profit factor, worst drawdown':50s}"
           f"  | OUT-OF-SAMPLE (held-out final year)")
    print("=" * 132)
    print(" All per lot, pooled across NIFTY / BANKNIFTY / SENSEX, real expiries, after costs")
    print(" Entries: today's real gates (unchanged). Only the exit differs between the two rows.")
    print(f" Variant: stop tightens to breakeven after {WAIT_BARS} bars (2h) if T1 is still not touched.")
    print("=" * 132)
    print(hdr)
    print("-" * 132)

    base = run_one(lambda A, i, tr: ps.simulate(A, i, tr))
    print(line("hold to T2/stop (live, today)", base))

    variant = run_one(simulate_time_breakeven)
    print(line(f"+ breakeven at {WAIT_BARS} bars if no T1 yet", variant))
    print("-" * 132)

    print("\n VERDICT — kept only if better than the live exit both in-sample and out-of-sample")
    di = variant["is"]["total"] - base["is"]["total"]
    do = variant["oos"]["total"] - base["oos"]["total"]
    dd = variant["oos"]["dd"] - base["oos"]["dd"]
    keep = di > 0 and do > 0
    print(f"   {'KEEP ' if keep else 'DROP '} time-based breakeven   in-sample {di:>+10,.0f}   "
          f"out-of-sample {do:>+10,.0f}   held-out drawdown {dd:>+9,.0f}")
    verdict_line = ("This clears the project's own bar to be adopted for real trades."
                    if keep else
                    "This does NOT clear the bar; no time-based breakeven is added anywhere.")
    print(f"\n   {verdict_line}")

    # A result this large (roughly double the profit, roughly half the drawdown, in BOTH
    # periods) earns the extra scrutiny btst_study.py's own ROBUST bar asks for - not trusted on
    # one pre-declared setting alone. Sweeping wait_bars checks whether this is a genuine,
    # broad effect or a fluke of exactly 8 bars - reported in full, not just whichever wins.
    print("\n" + "=" * 132)
    print(" ROBUSTNESS CHECK - is this a broad effect, or a fluke of exactly 8 bars?")
    print(" Same entries, same pricing, only wait_bars changes. KEEP needed at every value to trust it.")
    print("=" * 132)
    print(line("baseline (for reference)", base))
    sweep = {}
    all_keep = True
    for wb in (2, 4, 6, 8, 10, 12, 16, 20):
        r = run_one(lambda A, i, tr, wb=wb: simulate_time_breakeven(A, i, tr, wait_bars=wb))
        sweep[wb] = r
        wdi = r["is"]["total"] - base["is"]["total"]
        wdo = r["oos"]["total"] - base["oos"]["total"]
        wkeep = wdi > 0 and wdo > 0
        all_keep = all_keep and wkeep
        print(line(f"wait_bars={wb}", r) + f"   {'KEEP' if wkeep else 'drop'}")
    print(f"\n   {'Robust: KEEP at every wait_bars value swept.' if all_keep else 'NOT robust: at least one wait_bars value drops - treat the single-setting KEEP above with caution.'}")

    return {"live": base, "time_breakeven": variant, "sweep": sweep}


def price_bitcoin_trade(A, pos, tr, exit_fn, hold_bars=96, square_off=False, target="t2"):
    """One BTC trade's points P&L under either bt.run()'s own baked-in exit (exit_fn=None) or a
    custom exit function - CE profits when exit>entry, PE when exit<entry: points per contract,
    no option premium, since BTC is a Delta Exchange futures/perp position, not an option."""
    i = pos[tr["when"]]
    if exit_fn is None:
        exit_px = tr["exit"]
    else:
        legs = exit_fn(A, i, tr, hold_bars=hold_bars, square_off=square_off, target=target)
        exit_px = legs[0][0]
    return (exit_px - tr["entry"]) * (1.0 if tr["side"] == "CE" else -1.0)


def bitcoin_stats(pts):
    """n, total, profit factor and worst drawdown (equity walked in trade order) from a plain
    list of points-per-contract results - the same shape gann_volume_study.py's own BTC stats
    already use, reused here rather than re-derived."""
    if not pts:
        return None
    wins = [x for x in pts if x > 0]
    losses = [x for x in pts if x < 0]
    pf = (sum(wins) / -sum(losses)) if losses else float("inf")
    eq = peak = dd = 0.0
    for x in pts:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": len(pts), "total": sum(pts), "pf": pf, "dd": dd}


def study_bitcoin():
    """The user, 29 Sep 2026: "did you backtested on btc" - no, the study above only covers the
    Indian indices. This fills that gap, with its real limit stated up front: BTC's actual live
    entries come from the AI desk (an LLM decision, ai_desk.py), not the rule engine, and
    backtesting the AI's real decisions needs real (costly) LLM calls against 3 years of history -
    already declined earlier this session for exactly that reason. What CAN be tested without
    that: the EXIT mechanic itself, on BTC's own price action, using the same proxy entries
    gann_volume_study.py's own study_bitcoin() already established for this exact limitation -
    signal_engine's own technical bias, unfiltered by the Indian rule-book gates that do not
    apply to crypto anyway. The exit does not care how the entry was decided, only what price
    does afterward, so this is a meaningful check of the mechanic even though it cannot validate
    the AI's own entries. Priced in points per contract, not rupees - BTC is a Delta Exchange
    futures/perp position, not an option premium, so there is no Black-Scholes step here."""
    df = bt.fetch_history("BTC", years=3, use_cache=True)
    split = SPLIT.tz_convert(df.index.tz) if df.index.tz is not None else SPLIT.tz_localize(None)
    A = {"hi": df["High"].to_numpy(), "lo": df["Low"].to_numpy(), "cl": df["Close"].to_numpy(),
         "end": (pd.Series(df.index.date) != pd.Series(df.index.date).shift(-1)).to_numpy()}
    result = bt.run("BTC", df, gate=None)   # crypto: always_open -> hold_bars=96 (24h), square_off=False
    trades = result["trades"]
    pos = {t: n for n, t in enumerate(df.index)}

    def split_stats(exit_fn):
        is_pts, oos_pts = [], []
        for tr in trades:
            pts = price_bitcoin_trade(A, pos, tr, exit_fn)
            (is_pts if tr["when"] < split else oos_pts).append(pts)
        return {"is": bitcoin_stats(is_pts), "oos": bitcoin_stats(oos_pts)}

    def line(name, r):
        f = lambda s: (f"{s['n']:>5} {s['total']:>+10,.0f} pts PF {s['pf']:4.2f} DD {s['dd']:>8,.0f}"
                       if s else "   no trades")
        return f" {name:34s} {f(r['is'])}  | {f(r['oos'])}"

    print("\n" + "=" * 132)
    print(" BITCOIN - index points per contract, no option pricing. Proxy entries (signal_engine's")
    print(" own technical bias) - NOT the AI desk's real decisions, which cost real LLM calls to")
    print(" backtest and were declined earlier this session. Exit mechanic tested on real price action.")
    print("=" * 132)
    base = split_stats(None)
    print(line("hold to T2/stop (bt.run() default)", base))
    variant = split_stats(simulate_time_breakeven)
    print(line(f"+ breakeven at {WAIT_BARS} bars if no T1 yet", variant))
    print("-" * 132)
    print("\n VERDICT — kept only if better than the baseline both in-sample and out-of-sample")
    if base["is"] and base["oos"] and variant["is"] and variant["oos"]:
        di = variant["is"]["total"] - base["is"]["total"]
        do = variant["oos"]["total"] - base["oos"]["total"]
        keep = di > 0 and do > 0
        print(f"   {'KEEP ' if keep else 'DROP '} time-based breakeven (BTC)   in-sample {di:>+10,.0f} pts   "
              f"out-of-sample {do:>+10,.0f} pts")
    else:
        print("   too few trades in one period to compare")
    return {"live": base, "time_breakeven": variant}


if __name__ == "__main__":
    main()
    study_bitcoin()
