#!/usr/bin/env python3
"""
all_votes_study.py — should every vote have to agree before the rules take a trade? (3 years, Nifty / Sensex / Midcap)
================================================================================
The user, 21 Oct 2026, after the live Nifty 23050 CE and Sensex 74500 CE bought at 09:22 both stopped out (about
₹51,500 before charges over the two live accounts) - entered on MACD, RSI and VWAP with Trend and PCR not voting: "even
when pcr and trend doest count still the tool is giving a trade i want all 7 votes for the trade". The seven are the
Signal page's rows: Trend, MACD, RSI, VWAP, PCR, the ADX gate and room to run. The last two already have to pass; the
rule today on the five votes is at least 3 agreeing and at most 1 against (config.strictness() "strict").

PCR CANNOT BE TESTED: expired option chains are gone from Kite, so every 3-year study has PCR abstaining (it votes
only outside 0.80-1.20 anyway). So the nearest testable form of "all 7" is all FOUR other votes agreeing.

FIXED BEFORE ANY RESULT WAS SEEN (21 Oct 2026). The rules as live runs them today (order_block_study.rules_rows: the room
since 15 Oct, out at T3 with the stop to T1 / T2, the Supertrend trail, the 2-hour breakeven, the T1-exit re-entry, at
the money on the real expiry, 0.25% slippage, every charge, per lot; Sensex and Midcap asserted), with ONE more
condition on the reading the tool already acted on (a blocked signal frees the slot, as live):
  TODAY        no change - 3 of the votes agreeing, at most 1 against
  NO_AGAINST   3 agreeing and NONE against
  TREND_MUST   Trend must be one of the votes agreeing (Trend not voting, or against, blocks)
  ALL_FOUR     Trend, MACD, RSI and VWAP all agree - "all 7" without PCR, the most the history can test
Kept only if more than TODAY in BOTH periods (first two years to 15 Aug 2025, the held-out year), the three indices
pooled and on each one; robustness.py's walk-forward / Monte Carlo line under each verdict.

RESULT (21 Oct 2026, history to 11 Sep 2026; all_votes_study_results.txt). Profit against TODAY, pooled, first two
years / held-out (TODAY +348,446 / +194,328):
  NO_AGAINST  -62,053 / -78,689    DROP, better in 0 of 7 half-years
  TREND_MUST  -63,989 / +256       DROP pooled; Nifty alone passes (+735 / +22,502, 5 of 7 half-years, shallower drops),
                                   Sensex and Midcap lose - one index of three after the fact: not proven
  ALL_FOUR    -179,925 / -124,426  DROP, better in 0 of 7 half-years; Midcap's held-out year turns into a loss (-13,285)
The trades ALL_FOUR would have blocked (995 / 572) earned as much as the ones it keeps (PF 1.23 / 1.22 against
1.26 / 1.22): a fourth agreeing vote does not pick the better trades, it only takes fewer. Requiring PCR too ("all 7")
would block more still, untested.

    /usr/bin/python3 all_votes_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
VARIANTS = ("TODAY", "NO_AGAINST", "TREND_MUST", "ALL_FOUR")
CORE = ("Trend", "MACD", "RSI", "VWAP")


def passes(v, rec):
    """Whether variant v lets a reading the tool already acted on through."""
    if v == "TODAY":
        return True
    s = 1 if rec.get("option_type") == "CE" else -1
    votes = rec.get("votes") or {}
    if v == "NO_AGAINST":
        return not any(x == -s for x in votes.values())
    if v == "TREND_MUST":
        return votes.get("Trend") == s
    if v == "ALL_FOUR":
        return all(votes.get(k) == s for k in CORE)
    raise ValueError(v)


def _job(job):
    key, v = job
    import order_block_study as ob
    import reversal_exit_study as res
    real = res.live_gate
    res.live_gate = lambda k, i, rec, df, real=real: real(k, i, rec, df) and passes(v, rec)
    # every reading's votes, for the breakdown under TODAY
    seen = {}

    def make(df, pre):
        if v != "TODAY":
            return None, None, None
        return None, None, lambda i, p: p.update(seen.get(i, {}))
    if v == "TODAY":
        res.live_gate = lambda k, i, rec, df, real=real: (
            seen.__setitem__(i, {f"ok_{x}": passes(x, rec) for x in VARIANTS[1:]}) or real(k, i, rec, df))
    return job, ob.rules_rows(key, make)


def main():
    import multiprocessing as mp
    import order_block_study as ob
    import stop_day_study as sds
    jobs = [(k, v) for k in RULES for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, figs in ob.B_FIGS.items():
        s = sds.split(got[(k, "TODAY")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} TODAY must be room_fix_study's B {figs}"
    pool_ = lambda v: sorted([r for k in RULES for r in got[(k, v)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" EVERY VOTE MUST AGREE? the rules as live runs them, Nifty + Sensex + Midcap, per lot after costs, ₹  (columns:"
          " trades, won, total, profit factor, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" POOLED (the verdict)")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in RULES:
        print(f" {k}")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    print("\n TODAY'S TRADES, split by what each condition would have said of them")
    base = pool_("TODAY")
    for v in VARIANTS[1:]:
        yes = [r for r in base if r.get(f"ok_{v}")]
        no = [r for r in base if r.get(f"ok_{v}") is False]
        print(f"   {v}: lets through {len(yes)} of {len(base)}")
        print(sds.line("      let through", yes))
        print(sds.line("      blocked", no))
    print("\n VERDICT - kept only if more than TODAY in BOTH periods, pooled and then on each index")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(f"{v} pooled", pool_(v), pool_("TODAY")).strip())
        for k in RULES:
            print("      " + sds.verdict(f"{v} {k}", got[(k, v)], got[(k, "TODAY")]).strip())
    return got


if __name__ == "__main__":
    main()
