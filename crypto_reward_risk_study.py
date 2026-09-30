#!/usr/bin/env python3
"""crypto_reward_risk_study.py — what did raising crypto's MIN_REWARD_RISK_T3 from
1.0 to 2.5 actually do, on BTC's own history?

The user, 30 Sep 2026: first "i want to change crypto risk reward from 1:1 to
1:2.50" (built and deployed the same day - config.MIN_REWARD_RISK_T3 is now per-
market, {"nse_index": 1.0, "crypto": 2.5}, stated at the time as the user's own
choice and UNTESTED against crypto's own history on this gate), then "run a study
on crypto with 2.5". This is that study - retroactive, since the value is already
live, checking what it actually does rather than leaving it untested.

METHODOLOGY, AND ITS REAL LIMITS (same shape as time_breakeven_study.py's own
study_bitcoin() - see that file for the fuller explanation of this limitation)
    BTC's real live entries come from the AI desk (an LLM decision, ai_desk.py).
    Backtesting the AI's actual decisions needs real, costly LLM calls against 3
    years of history - already declined earlier this session for exactly that
    reason. What CAN be tested honestly without that: tickets.py's own
    _reward_hold() - the exact gate, called directly rather than a paraphrase of
    it, the same technique reversal_exit_study.py already established for this
    project - against backtest_intraday.py's real technical-bias proxy entries on
    BTC's real 15-minute price history: signal_engine.build_recommendation() and
    compute_reachability(), the SAME functions the live tool calls, not a
    simplified stand-in. Priced in index points per contract, not rupees or a real
    option premium - BTC is a Delta Exchange futures/perp position on the live
    tool, and there is no historical option chain to price a premium against
    anyway (see backtest_intraday.py's own module docstring, "CANNOT: Option
    premiums").

    Of compute_reachability()'s three limits, only the day-range one is live here
    - there is no historical option-chain OI/straddle data for BTC to feed the
    other two (same limit backtest_intraday.py already states for the Indian
    indices' own PCR/expected-move votes). So T3 here, and therefore the
    reward:risk ratio this gate actually measures, is narrower than a real chain
    would sometimes make it live - which makes the 2.5 bar HARDER to clear here
    than it would be with a real chain, not easier. Worth stating plainly, and it
    is a bias toward caution, not toward a flattering result.

    Only the reward:risk gate is toggled between the two runs. Every other entry
    gate backtest_intraday.run() already applies live to every backtest (ADX,
    momentum, RSI-divergence, the day-range room check inside
    build_recommendation() itself) is identical in both.

    python3 crypto_reward_risk_study.py
"""
import backtest_intraday as bt
import config
import pro_study as ps
import tickets

SPLIT = ps.SPLIT

_SELF = object()          # tickets.TicketBook._reward_hold is stateless (verified below);
                          # a real instance is never needed, only something to bind as `self`


def _assert_reward_hold_stateless():
    """Fails loudly, at import time, if a future edit makes this method touch
    self/book state - better than silently producing a wrong-but-plausible number,
    the same discipline reversal_exit_study.py already applies to the other three
    gates it calls this same way."""
    rec = {"spot": 100.0, "risk_points": 10.0, "index_targets": [None, None, 125.0]}
    tickets.TicketBook._reward_hold(_SELF, "BTC", rec)


_assert_reward_hold_stateless()


def reward_gate(i, rec):
    """Today's real _reward_hold(), called directly. Reads config.MIN_REWARD_RISK_T3
    fresh on every call (confirmed by reversal_exit_study_test.py's own section 1),
    so main() below controls which value is in force simply by setting it before
    each bt.run() - not a second copy of the gate's logic."""
    return tickets.TicketBook._reward_hold(_SELF, "BTC", rec) is None


def price_bitcoin_trade(tr):
    """Points per contract, signed for the side - no option premium (see module
    docstring). Only the entry gate changes between the two runs below; the exit
    is whatever bt.run() itself already produces (T3 or stop), untouched."""
    return (tr["exit"] - tr["entry"]) * (1.0 if tr["side"] == "CE" else -1.0)


def bitcoin_stats(pts):
    """n, total, profit factor and worst drawdown (equity walked in trade order) -
    the same shape time_breakeven_study.py's own bitcoin_stats() already uses."""
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


def split_stats(trades):
    is_pts, oos_pts = [], []
    for tr in trades:
        pts = price_bitcoin_trade(tr)
        (is_pts if tr["when"] < SPLIT else oos_pts).append(pts)
    return {"is": bitcoin_stats(is_pts), "oos": bitcoin_stats(oos_pts)}


def line(name, r):
    f = lambda s: (f"{s['n']:>5} {s['total']:>+10,.0f} pts  PF {s['pf']:4.2f}  DD {s['dd']:>8,.0f}"
                   if s else "   no trades")
    return f" {name:38s} {f(r['is'])}  | {f(r['oos'])}"


SWEEP = (0, 0.5, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0)
# 0 disables the gate outright (config.py's own convention) - the true baseline with no
# filter at all. 1.0 is what crypto had until 30 Sep 2026; 2.5 is today's deployed value.
# The rest exist to find the best value for profit, not just confirm 2.5 - the user, 30
# Sep 2026: "test which risk reward will be better for crypto for better profits."


def best_by_period(results, period):
    """The swept value with the highest total in one period ("is" or "oos"), or None if
    every value had too few trades in that period to compare."""
    candidates = [(v, results[v][period]["total"]) for v in SWEEP if results[v][period]]
    if not candidates:
        return None
    return max(candidates, key=lambda vt: vt[1])[0]


def robust_best(results):
    """The swept value that is genuinely good for profit, not just the one that happens to
    top ONE period - the same 'must hold up in both periods' bar this project applies to
    every other threshold, applied here to picking the best candidate rather than to a
    single before/after comparison.

    Ranks every value by total profit within each period separately, then picks whichever
    value has the best WORST rank across the two - a value has to place well in BOTH to
    win this, not spike in one while doing badly in the other. Ties broken by the higher
    combined rank. None if either period has no comparable results at all."""
    def ranks(period):
        ordered = sorted((v for v in SWEEP if results[v][period]),
                         key=lambda v: results[v][period]["total"], reverse=True)
        return {v: i for i, v in enumerate(ordered)}       # 0 = best in this period
    r_is, r_oos = ranks("is"), ranks("oos")
    common = [v for v in SWEEP if v in r_is and v in r_oos]
    if not common:
        return None
    return min(common, key=lambda v: (max(r_is[v], r_oos[v]), r_is[v] + r_oos[v]))


def main():
    df = bt.fetch_history("BTC", years=3, use_cache=True)
    _saved = config.MIN_REWARD_RISK_T3
    results = {}
    try:
        print("\n" + "=" * 132)
        print(" CRYPTO REWARD:RISK GATE - which value is actually best for profit? 0 = no gate, 1.0 = what crypto")
        print(" had before 30 Sep 2026, 2.5 = today's deployed value.")
        print(" BTC, index points per contract. Proxy entries (signal_engine's own technical bias) - NOT the")
        print(" AI desk's real LLM decisions, which cost real money to backtest and were declined this session.")
        print(" Exit is bt.run()'s own baked-in T3-or-stop, unchanged across every value swept.")
        print("=" * 132)

        for need in SWEEP:
            config.MIN_REWARD_RISK_T3 = {"nse_index": 1.0, "crypto": need}
            res = bt.run("BTC", df, gate=reward_gate)
            results[need] = split_stats(res["trades"])
            tag = "  (no gate)" if need == 0 else "  (before today)" if need == 1.0 else \
                  "  (today's live value)" if need == 2.5 else ""
            print(line(f"need >= {need:g}{tag}", results[need]))

        print("-" * 132)
        best_is, best_oos = best_by_period(results, "is"), best_by_period(results, "oos")
        print(f"\n Best on total points, in-sample alone: need >= {best_is:g} "
              f"({results[best_is]['is']['total']:>+,.0f} pts)")
        print(f" Best on total points, held-out alone:  need >= {best_oos:g} "
              f"({results[best_oos]['oos']['total']:>+,.0f} pts)")
        if best_is == best_oos:
            print(f" They AGREE: need >= {best_is:g} is the best single value in both periods.")
        else:
            print(" They DISAGREE - whichever tops one period is not the one that tops the other.")
            print(" Picking by in-sample alone here would be exactly the overfitting trap this project's")
            print(" own standing practice exists to catch.")

        robust = robust_best(results)
        if robust is not None:
            r = results[robust]
            print(f"\n ROBUST PICK (best worst-of-both-periods rank, ties broken by combined rank): "
                  f"need >= {robust:g}")
            print(f"   in-sample {r['is']['total']:>+,.0f} pts, PF {r['is']['pf']:.2f}, DD {r['is']['dd']:,.0f}")
            print(f"   held-out  {r['oos']['total']:>+,.0f} pts, PF {r['oos']['pf']:.2f}, DD {r['oos']['dd']:,.0f}")

        print("\n Today's deployed value (2.5) against this same sweep:")
        i25, o25 = results[2.5]["is"], results[2.5]["oos"]
        print(f"   in-sample rank {sorted(SWEEP, key=lambda v: -(results[v]['is']['total'] if results[v]['is'] else float('-inf'))).index(2.5) + 1} of {len(SWEEP)}"
              f", held-out rank {sorted(SWEEP, key=lambda v: -(results[v]['oos']['total'] if results[v]['oos'] else float('-inf'))).index(2.5) + 1} of {len(SWEEP)}")

        # Monotonic check: does total profit fall smoothly and steadily as the bar rises,
        # or spike/dip at particular values - the thing a single best-value number alone
        # cannot show, and the reason to distrust a peak that sits alone on a jagged curve.
        totals_is = [results[n]["is"]["total"] if results[n]["is"] else None for n in SWEEP]
        totals_oos = [results[n]["oos"]["total"] if results[n]["oos"] else None for n in SWEEP]
        pairs_is = [(a, b) for a, b in zip(totals_is, totals_is[1:]) if a is not None and b is not None]
        pairs_oos = [(a, b) for a, b in zip(totals_oos, totals_oos[1:]) if a is not None and b is not None]
        smooth_is = all(b <= a for a, b in pairs_is) or all(b >= a for a, b in pairs_is)
        smooth_oos = all(b <= a for a, b in pairs_oos) or all(b >= a for a, b in pairs_oos)
        if pairs_is and pairs_oos:
            print(f"\n {'A smooth trend' if smooth_is and smooth_oos else 'NOT a smooth trend'} across the "
                  f"sweep in both periods - {'trust the peak.' if smooth_is and smooth_oos else 'treat any single peak with real caution.'}")
    finally:
        config.MIN_REWARD_RISK_T3 = _saved
    return results


if __name__ == "__main__":
    main()
