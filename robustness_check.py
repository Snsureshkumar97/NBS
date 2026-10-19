#!/usr/bin/env python3
"""
robustness_check.py — the two new checks (robustness.py) run on the live systems and on the decisions already taken
================================================================================
The user, 20 Oct 2026: "yes add those two checks" (walk-forward and a Monte Carlo drawdown, after the backtesting-
frameworks guide on skills.sh). Run once on what is live and on what was decided under the old two-period rule alone:
  1. RISK OF EACH LIVE SYSTEM - the Monte Carlo worst drop (typical, and in 95 of 100 redraws of its trading days) and
     the chance of a loss: the Indian rules (Nifty, Sensex, Midcap, on today's room - room_fix_study's B), Bank Nifty's
     TradingView rating, gold's MA rating at 1:1, Bitcoin's engine. Per lot (gold: per gold lot; BTC: per BTC).
  2. DECISIONS RE-CHECKED, Nifty + Sensex + Midcap pooled unless named:
       T3 exit (live since 13 Oct)            t3re against t2re (t3_exit_recheck_study)
       T1-exit re-entry (live since 13 Oct)   t3re against t3 (the same exit without it)
       room from the day's range (built 14 Oct, rolled back 15 Oct)   room_fix_study's C against B
       Bank Nifty FVG (order_block_study, declined 20 Oct)            its TradingView rating with / without
  3. WALK-FORWARD PICK - had the setting been re-chosen every half-year as the best of the year before:
       gold's take-profit (gold_tp_study: 3, 5, 8, 10, 30 x ATR)      the Indian exit (t2re / t3 / t3re)
Every base asserted to its own study's figure. Nothing here changes anything live.

RESULT (20 Oct 2026, Indian history to 11 Sep 2026, Exness ticks to Aug 2026; robustness_check_results.txt):
  RISK, all three years, worst drop typical / in 95 of 100, and the chance a period ends in a loss (first two / held-out):
    Indian rules, Nifty + Sensex + Midcap (a lot each)  ₹71,366 / ₹121,442 (the actual: ₹51,373 / ₹84,323)   0% / 2%
      Nifty 9% / 6%, Sensex 4% / 5%, Midcap 0% / 6% on their own
    Bank Nifty TradingView  ₹69,141 / ₹125,531   4% / 12%
    Gold 1:1  $109,082 / $221,358 per gold lot   48% / 29% - a coin flip
    Bitcoin engine  $60,179 / $108,489 per BTC   89% / 56% - a losing system
  DECISIONS: T3 exit STEADY (7 of 7 half-years). T1-exit re-entry KEEP, FRAGILE (4 of 7 - more profit, not every
  stretch). Room from the day's range (rolled back 15 Oct) KEEP, FRAGILE: 95% worst drop ₹143,208 / ₹137,063 against
  ₹106,874 / ₹103,025 - more profit, deeper swings; Midcap drop. Bank Nifty FVG (declined) STEADY on Bank Nifty
  (5 of 7, shallower drops) - still loses on Nifty's TradingView, the reason it stays off. Gold 1:1 beat 30 x ATR in 1 of
  7 half-years.
  WALK-FORWARD PICK: gold's re-chosen target $+198,135 against fixed 8 x ATR $+226,682 / 10 x $+210,544 / 30 x
  $+200,926 / 3 x (live) $+89,341 - re-choosing does not beat a fixed far target. The Indian exit re-chosen picks t3re
  (live) in every half-year.

    /usr/bin/python3 robustness_check.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
B_T3RE = {"SENSEX": (78098, 55603), "MIDCPNIFTY": (209886, 86911)}     # room_fix_study B
C_T3RE = {"SENSEX": (225647, 119188), "MIDCPNIFTY": (264301, 85500)}   # room_fix_study C
GOLD = {3.0: (2174, 55620), 30.0: (48403, 145212)}                    # gold_tp_study (3 x ATR via config's note)


def _job(job):
    kind, key = job[0], job[1]
    if kind in ("B", "C"):
        import live_room_study as lrs
        import t3_exit_recheck_study as t3
        lrs.install_live_room(key, chain=(kind == "B"), normal_days=10)
        _, out = t3._job(key)
        return job, out
    import order_block_study as ob
    return job, ob._tv_job(("BANKNIFTY", job[2]))[1]


def _gold_month(args):
    import gold_tp_study as gtp
    return gtp._job(args)


def gold_sweep():
    from concurrent.futures import ProcessPoolExecutor
    import cfd_tick_study as t
    import crypto_strategy_study as cs
    import crypto_tv_ratings_study as cts
    import exness_data as ed
    import gold_tp_study as gtp
    _, path = cts._prep("XAUUSD")
    rows = []
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as ex:
        for part in ex.map(_gold_month, [(y, m, path) for (y, m) in ed.months_until(t.LAST)]):
            rows.extend(part)
    cts.add_swap_and_direction(rows, "XAUUSD", ed.load("XAUUSD")[["Open", "High", "Low", "Close", "Volume"]])
    res = {k: cs.sequential(sorted([r for r in rows if r["tp"] == k], key=lambda r: r["when"])) for k in gtp.TPS}
    for k, figs in GOLD.items():
        p = cs.periods(res[k])
        got = (round(cs.stats(p["is"], "net")["total"]), round(cs.stats(p["oos"], "net")["total"]))
        assert all(abs(a - b) <= 2 for a, b in zip(got, figs)), f"gold {k} x ATR must be {figs}, got {got}"
    return res


def main():
    import multiprocessing as mp
    import config
    import crypto_strategy_study as cs
    import order_block_study as ob
    import robustness as rb
    import stop_day_study as sds
    jobs = [("B", k) for k in RULES] + [("C", k) for k in RULES] + [("TV", "BANKNIFTY", v) for v in ("BASE", "FVG")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_job, jobs, chunksize=1))
    for k, figs in B_T3RE.items():
        s = sds.split(got[("B", k)]["t3re"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} B t3re must be {figs}"
    for k, figs in C_T3RE.items():
        s = sds.split(got[("C", k)]["t3re"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} C t3re must be {figs}"
    s = sds.split(got[("TV", "BANKNIFTY", "BASE")])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == ob.TV_FIGS, "Bank Nifty TV base"
    gold = gold_sweep()
    btc_rows, _ = ob.crypto_rows("BTCUSD")
    btc = cs.sequential(sorted(btc_rows, key=lambda r: r["when"]), cooldown_min=config.REENTRY_COOLDOWN_MIN,
                        waiver_adx=config.ADX_TREND_THRESHOLD)
    p = cs.periods(btc)
    assert (round(cs.stats(p["is"], "net")["total"]), round(cs.stats(p["oos"], "net")["total"])) == ob.BTC_FIGS
    btc = p["is"] + p["oos"]
    pool_ = lambda kind, v: sorted([r for k in RULES for r in got[(kind, k)][v]], key=lambda r: r["when"])

    W = 170
    print("=" * W)
    print(" 1. RISK OF EACH LIVE SYSTEM - Monte Carlo on its trading days (5,000 redraws): the worst drop, typical and in"
          " 95 of 100, and the chance the period ends in a loss")
    print("=" * W)
    print(" Indian rules, Nifty + Sensex + Midcap (per lot each, ₹)")
    print(sds.line("   today", pool_("B", "t3re")))
    print(rb.risk_line(pool_("B", "t3re"), sds.SPLIT, "₹"))
    for k in RULES:
        print(sds.line(f"   {k}", got[("B", k)]["t3re"]))
        print(rb.risk_line(got[("B", k)]["t3re"], sds.SPLIT, "₹"))
    print(sds.line("   BANK NIFTY (TradingView rating)", got[("TV", "BANKNIFTY", "BASE")]))
    print(rb.risk_line(got[("TV", "BANKNIFTY", "BASE")], sds.SPLIT, "₹"))
    gold_live = cs.periods(gold[3.0])
    print(" GOLD, TradingView MA rating 1:1 ($ per gold lot)")
    print(rb.risk_line(gold_live["is"] + gold_live["oos"], sds.SPLIT, "$"))
    print(" BITCOIN, the tool's crypto votes ($ per BTC)")
    print(rb.risk_line(btc, sds.SPLIT, "$"))

    print("\n" + "=" * W)
    print(" 2. DECISIONS RE-CHECKED - the two-period verdict as it was, with the walk-forward and Monte Carlo line under it")
    print("=" * W)
    print("  " + sds.verdict("T3 exit (t3re) vs T2 (t2re) - LIVE", pool_("B", "t3re"), pool_("B", "t2re")).strip())
    print("  " + sds.verdict("T1-exit re-entry (t3re) vs none (t3) - LIVE", pool_("B", "t3re"), pool_("B", "t3")).strip())
    print("  " + sds.verdict("room from the day's range (C) vs today's (B)", pool_("C", "t3re"), pool_("B", "t3re")).strip())
    for k in RULES:
        print("      " + sds.verdict(f"room C vs B {k}", got[("C", k)]["t3re"], got[("B", k)]["t3re"]).strip())
    print("  " + sds.verdict("Bank Nifty FVG (declined)", got[("TV", "BANKNIFTY", "FVG")],
                             got[("TV", "BANKNIFTY", "BASE")]).strip())
    g30, g3 = cs.periods(gold[30.0]), cs.periods(gold[3.0])
    print("   (gold) 1:1 target (live, the user's choice 15 Oct) vs 30 x ATR:")
    print(rb.verdict_line(g3["is"] + g3["oos"], g30["is"] + g30["oos"], sds.SPLIT, "$", passed=False))

    print("\n" + "=" * W)
    print(" 3. WALK-FORWARD PICK - re-choosing every half-year the setting that made the most over the year before")
    print("=" * W)
    gold_v = {f"{k:g} x ATR": (lambda p: p["is"] + p["oos"])(cs.periods(gold[k])) for k in gold}
    wp = rb.walk_forward_pick(gold_v)
    print(f"   GOLD take-profit: re-chosen ${wp['picked']:+,.0f}  |  fixed, same half-years: "
          + "  ".join(f"{n} ${v:+,.0f}" for n, v in wp["fixed"].items()))
    print("     choices: " + "  ".join(f"{h} {n}" for h, n in wp["choices"]))
    ex_v = {v: pool_("B", v) for v in ("t2re", "t3", "t3re")}
    wp = rb.walk_forward_pick(ex_v)
    print(f"   INDIAN exit: re-chosen ₹{wp['picked']:+,.0f}  |  fixed, same half-years: "
          + "  ".join(f"{n} ₹{v:+,.0f}" for n, v in wp["fixed"].items()))
    print("     choices: " + "  ".join(f"{h} {n}" for h, n in wp["choices"]))
    return got


if __name__ == "__main__":
    main()
