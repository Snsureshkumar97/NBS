#!/usr/bin/env python3
"""
votes_alone_study.py — each vote on its own, and each gate on its own (six years, all four indices)
================================================================================
The user, 28 Oct 2026: "we have votes check each vote individually for example only with rsi only with vwap only with
adx gate".

Today the rules take a trade when at least 3 of Trend, MACD, RSI and VWAP agree with at most 1 against (PCR is the
fifth vote live; expired chains are gone, so the history cannot test it), MACD is not against, ADX is 20 or more
(the ADX gate), and there is room to run (room / risk at least 0.6 at the signal, and T3 at least 1x the risk at the
ticket). The user's accounts run "all must agree": Trend, MACD, RSI and VWAP all the trade's way.

FIXED BEFORE ANY RESULT WAS SEEN (28 Oct 2026). The rules as live runs them (order_block_study.rules_rows: the live
room, out at T3 with the stop to T1 / T2, the Supertrend trail, the 2-hour breakeven, cooldown, T1-exit re-entry,
at the money on the real expiry, slippage, every charge, per lot); only the entry changes:
  TODAY        3 of 4 agree, at most 1 against, MACD not against, ADX gate, room             (the tested rule)
  ALL_AGREE    all four agree, + the gates                              (what the user's accounts run live)
  ONE VOTE, the gates kept: the trade goes the vote's way, nothing else votes (ADX gate and room as today)
               TREND_ONLY  MACD_ONLY  RSI_ONLY  VWAP_ONLY
  ONE VOTE, nothing else at all (no ADX gate, no room check)
               TREND_BARE  MACD_BARE  RSI_BARE  VWAP_BARE
  ONE GATE, today's votes kept
               ADX_GATE_ONLY   the ADX gate, no room check
               ROOM_ONLY       the room check, no ADX gate
               NO_GATES        neither
BETTER only if a variant makes more than TODAY in BOTH periods (to 15 Aug 2025, then held out), the four indices
pooled, with robustness.py's walk-forward / Monte Carlo line under it (stop_day_study.verdict). Run with HOME at the
six-year history.

RESULT (28 Oct 2026, history to 26 Oct 2026; votes_alone_study_results.txt) - NOTHING HERE CAN BE READ AS IT STANDS:
THE WALK PAYS A COIN. Every loosening made more (MACD alone +2,679,687 / +719,891 against TODAY's +707,391 /
+247,972), so the controls were run - and a coin flip for the direction made +7,310,544 / +1,834,300, trading AGAINST
MACD +9,095,465 / +2,246,341. Cause: the walk fills every stop at the stop's own price. When the stop has stepped to T1
(or T2, or the Supertrend line - which jumps to the far side of the price when it flips) after a candle that touched
it and closed back below, the next candle opens already past the stop and is still sold AT the stop: a free exit at
T1 for any trade whose wick reached it. Filled at the open when the candle opens past the stop (as a live stop would
fill), the coins lose (-1,430,509 / -645,992 and -1,260,650 / -536,946, index move caught ~0.00%), against-MACD loses
most (-1,753,190 / -874,127), and the order is TODAY -99,851 / -22,028 > VWAP alone -244,268 / -117,733 > MACD alone
-422,900 / -157,398 > coins > against MACD. So: the votes do carry direction (TODAY catches +0.012% of the index a
trade, a coin ~0), today's 3-of-4 is the best entry tested, and the rules as a whole are about break-even after costs
over six years once stops fill honestly (Midcap +39,354 / +14,000, Sensex -13,273 / +17,619, Nifty -16,621 / +4,480,
Bank Nifty -109,311 / -58,126). Every study that walked a MOVING stop (t3_exit_recheck_study.sim,
opening_window_study.simulate_live and those built on them) shares the flaw; a fixed stop (the big candle's
indian_strategies_study.walk) only meets it on a gap between candles.

    /usr/bin/python3 votes_alone_study.py             (the variants)
    /usr/bin/python3 votes_alone_study.py controls    (the opposite of MACD and coin flips, same harness)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
CORE = ("Trend", "MACD", "RSI", "VWAP")
VARIANTS = (["TODAY", "ALL_AGREE"] + [f"{v.upper()}_ONLY" for v in CORE] + [f"{v.upper()}_BARE" for v in CORE]
            + ["ADX_GATE_ONLY", "ROOM_ONLY", "NO_GATES"])
# CONTROLS, added after the first run (28 Oct) because every loosening made more: the harness itself must not pay a
# trade for being a trade. MACD's slot carries the opposite of MACD, or a coin (a hash of the index and the candle's
# close, two salts) - if these also make money, the votes' numbers above mean nothing.
CONTROLS = ("MACD_AGAINST_ONLY", "COIN_A_ONLY", "COIN_B_ONLY", "COIN_A_BARE")


def setup(v):
    """Set this worker process's config for variant v -> extra gate on the reading (rec) or None."""
    import config
    import tickets
    if v in CONTROLS:
        import zlib
        import signal_engine as se
        real = se.build_recommendation
        mode = v                                      # v is rebound below; the wrapper keeps the control's name

        def wrapped(index_key, tech, *a, **k):
            if mode == "MACD_AGAINST_ONLY":
                s = -tech.get("macd_score", 0)
            else:
                s = 1 if zlib.crc32(f"{mode[5]}|{index_key}|{tech.get('last_close')}".encode()) % 2 else -1
            return real(index_key, dict(tech, macd_score=s), *a, **k)
        se.build_recommendation = wrapped
        v = "MACD_BARE" if v.endswith("_BARE") else "MACD_ONLY"
    one = next((c for c in CORE if v in (f"{c.upper()}_ONLY", f"{c.upper()}_BARE")), None)
    adx_off = v.endswith("_BARE") or v in ("ROOM_ONLY", "NO_GATES")
    room_off = v.endswith("_BARE") or v in ("ADX_GATE_ONLY", "NO_GATES")
    vo = {}
    if one:
        vo.update({"drop": [c for c in CORE if c != one], "min_agree": 1, "max_dissent": 0})
        config.MACD_MUST_AGREE = False                # the momentum veto is a second vote; MACD_ONLY has it anyway
    if adx_off:
        vo["adx"] = 0
    config.VOTE_OVERRIDES = dict(config.VOTE_OVERRIDES, nse_index=vo)
    if room_off:
        config._STRICTNESS = {k: dict(s, min_rr=0) for k, s in config._STRICTNESS.items()}
        tickets.reward_risk_t3 = lambda name, rec: (None, 0)
    if v == "ALL_AGREE":
        return lambda rec: all((rec.get("votes") or {}).get(c) == (1 if rec.get("option_type") == "CE" else -1)
                               for c in CORE)
    return None


def _job(job):
    """(key, variant) or (key, variant, "gap"): "gap" fills a stop at the candle's OPEN when the candle opens already
    past it (the walk otherwise fills every stop at the stop's own price). Each row carries the index move it caught,
    % of the entry, its way."""
    key, v = job[:2]
    gap = job[2:] == ("gap",)
    import order_block_study as ob
    import reversal_exit_study as res
    import t3_exit_recheck_study as t3
    extra = setup(v)
    real = res.live_gate
    real_sim = t3.sim
    seen, pts, opens = {}, {}, {}

    def gate(k, i, rec, df):
        seen[i] = sorted(c for c, x in (rec.get("votes") or {}).items() if x)
        return real(k, i, rec, df) and (extra is None or extra(rec))

    def sim(A, i, tr, st, target, **kw):
        legs, how, t1_done = real_sim(A, i, tr, st, target, **kw)
        px, j, w = legs[-1]
        ce = tr["side"] == "CE"
        if gap and how == "stop":
            px = min(px, opens["o"][j]) if ce else max(px, opens["o"][j])
            legs = legs[:-1] + [(px, j, w)]
        pts[i] = (px - tr["entry"]) / tr["entry"] * 100 * (1 if ce else -1)
        return legs, how, t1_done
    res.live_gate = gate
    t3.sim = sim

    def make(df, pre):
        opens["o"] = df["Open"].to_numpy(float)
        return None, None, lambda i, p: p.update(voters=seen.get(i), pts=pts.get(i))
    return job, ob.rules_rows(key, make)


def controls():
    import multiprocessing as mp
    import pandas as pd
    import stop_day_study as sds
    names = ("TODAY", "MACD_ONLY", "VWAP_ONLY", "MACD_AGAINST_ONLY", "COIN_A_ONLY", "COIN_B_ONLY")
    fills = ("walk", "gap")
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, [(k, v, f) for f in fills for k in KEYS for v in names], chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    for scope, keys in (("ALL FOUR POOLED", KEYS),) + tuple((k, (k,)) for k in KEYS):
        print("=" * 120)
        print(scope, "  CONTROLS  (to 15 Aug 2025 | held out)   then: index move caught, mean % of entry")
        for f in fills:
            print(f"  stops filled: {'at the stop (as every study)' if f == 'walk' else 'at the open when it opens past'}")
            for v in names:
                rows = s([r for k in keys for r in got[(k, v, f)]])
                mv = sum(r["pts"] for r in rows) / max(len(rows), 1)
                print(sds.line("     " + v, rows) + f"   {mv:+.3f}%")


def main():
    import multiprocessing as mp
    import pandas as pd
    import stop_day_study as sds
    jobs = [(k, v) for k in KEYS for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    s = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    groups = (("BASELINES", ("TODAY", "ALL_AGREE")),
              ("ONE VOTE, ADX gate and room kept", [f"{c.upper()}_ONLY" for c in CORE]),
              ("ONE VOTE, nothing else", [f"{c.upper()}_BARE" for c in CORE]),
              ("ONE GATE, today's votes kept", ("ADX_GATE_ONLY", "ROOM_ONLY", "NO_GATES")))
    for scope, keys in (("ALL FOUR POOLED", KEYS),) + tuple((k, (k,)) for k in KEYS):
        R = {v: s([r for k in keys for r in got[(k, v)]]) for v in VARIANTS}
        print("=" * 120)
        print(scope, "  rules, per lot after costs  (to 15 Aug 2025 | held out)")
        for title, vs in groups:
            print(f"  {title}")
            for v in vs:
                print(sds.line("     " + v, R[v]))
        if scope == "ALL FOUR POOLED":
            for v in VARIANTS[1:]:
                print("   " + sds.verdict(f"{v} vs TODAY", R[v], R["TODAY"]).strip().replace("\n", "\n   "))
        else:
            for v in VARIANTS[1:]:
                print("   " + sds.verdict(f"{v} vs TODAY", R[v], R["TODAY"]).splitlines()[0].strip())
    # sanity: a ONE-vote trade had that vote for it, and TODAY's had 3+ voters
    for k in KEYS:
        for c in CORE:
            rows = got[(k, f"{c.upper()}_ONLY")]
            assert all(r.get("voters") == [c] for r in rows), (k, c, {tuple(r.get("voters") or ()) for r in rows})
        assert all(len(r.get("voters") or ()) >= 3 for r in got[(k, "TODAY")]), k


if __name__ == "__main__":
    controls() if sys.argv[1:] == ["controls"] else main()
