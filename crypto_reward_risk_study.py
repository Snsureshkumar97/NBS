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


SWEEP = (1.0, 1.5, 2.0, 2.5, 3.0)     # 1.0 is what crypto had until 30 Sep 2026; 2.5 is
                                       # today's deployed value. The others are here only
                                       # to show whether 2.5 sits on a smooth trend or is
                                       # a spike at one lucky number - the same discipline
                                       # every other threshold this project has adopted was
                                       # held to (e.g. the stalled-trend ATR sweep, the
                                       # time-breakeven wait_bars sweep).


def main():
    df = bt.fetch_history("BTC", years=3, use_cache=True)
    _saved = config.MIN_REWARD_RISK_T3
    results = {}
    try:
        print("\n" + "=" * 132)
        print(" CRYPTO REWARD:RISK GATE - swept 1.0 (what crypto had until 30 Sep 2026) to 3.0, "
              "2.5 is today's deployed value")
        print(" BTC, index points per contract. Proxy entries (signal_engine's own technical bias) - NOT the")
        print(" AI desk's real LLM decisions, which cost real money to backtest and were declined this session.")
        print(" Exit is bt.run()'s own baked-in T3-or-stop, unchanged across every value swept.")
        print("=" * 132)

        for need in SWEEP:
            config.MIN_REWARD_RISK_T3 = {"nse_index": 1.0, "crypto": need}
            res = bt.run("BTC", df, gate=reward_gate)
            results[need] = split_stats(res["trades"])
            label = f"need >= {need:g}" + ("  (before today)" if need == 1.0 else
                     "  (today's live value)" if need == 2.5 else "")
            print(line(label, results[need]))

        print("-" * 132)
        before_stats, after_stats = results[1.0], results[2.5]
        bi, bo = before_stats["is"], before_stats["oos"]
        ai, ao = after_stats["is"], after_stats["oos"]
        if bi and bo and ai and ao:
            di = ai["total"] - bi["total"]
            do = ao["total"] - bo["total"]
            better = di > 0 and do > 0
            print(f"\n 2.5 vs 1.0 - {'BETTER' if better else 'NOT BETTER'} in BOTH periods on total points: "
                  f"in-sample {di:>+10,.0f} pts   held-out {do:>+10,.0f} pts")
            print(f" Trades taken: {bi['n']} -> {ai['n']} in-sample, {bo['n']} -> {ao['n']} held-out "
                  f"({100 * (1 - ai['n'] / bi['n']):.0f}% / {100 * (1 - ao['n'] / bo['n']):.0f}% fewer).")
            dd_i = bi["dd"] - ai["dd"]
            dd_o = bo["dd"] - ao["dd"]
            print(f" Worst drawdown: {'shallower' if dd_i > 0 and dd_o > 0 else 'mixed'} in both periods "
                  f"({bi['dd']:,.0f} -> {ai['dd']:,.0f} in-sample, {bo['dd']:,.0f} -> {ao['dd']:,.0f} held-out).")
        else:
            print("\n too few trades in one period to compare 2.5 against 1.0")

        # Monotonic check: does total profit fall smoothly and steadily as the bar rises,
        # or spike/dip at one value - the thing a single before/after number cannot show.
        totals_is = [results[n]["is"]["total"] if results[n]["is"] else None for n in SWEEP]
        totals_oos = [results[n]["oos"]["total"] if results[n]["oos"] else None for n in SWEEP]
        pairs_is = [(a, b) for a, b in zip(totals_is, totals_is[1:]) if a is not None and b is not None]
        pairs_oos = [(a, b) for a, b in zip(totals_oos, totals_oos[1:]) if a is not None and b is not None]
        smooth_is = all(b <= a for a, b in pairs_is) or all(b >= a for a, b in pairs_is)
        smooth_oos = all(b <= a for a, b in pairs_oos) or all(b >= a for a, b in pairs_oos)
        print(f"\n {'A smooth trend' if smooth_is and smooth_oos else 'NOT a smooth trend'} across the sweep in "
              f"both periods, not a spike at 2.5 specifically" if pairs_is and pairs_oos else "")
    finally:
        config.MIN_REWARD_RISK_T3 = _saved
    return results


if __name__ == "__main__":
    main()
