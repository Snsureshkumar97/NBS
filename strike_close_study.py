#!/usr/bin/env python3
"""
strike_close_study.py — enter only after a candle closes beyond the strike the tool gave
================================================================================
The user, 22 Oct 2026: "whichever the tool gives the strike i am noticing its giving the strike before confirmation
can we test whatever trade it gives it should only after the candle closes below the strike level". The tool buys the
strike at the money (the nearest to the index), so the index sits within half a strike step of it when the trade is
given; this waits for the index to CLOSE through that strike - below it for a put, above it for a call - before buying.

FIXED BEFORE ANY RESULT WAS SEEN (22 Oct 2026). The strike is the one the tool gives when its signal first appears (the
strike nearest the index at that 15-minute close); the signal "still holds" while every following 15-minute close
passes the live entry checks the same way. Each index on the system it runs live:
  the rules on Nifty, Sensex and Midcap (order_block_study.rules_rows - today's room, out at T3 with the steps, the
  trail, the breakeven, the T1-exit re-entry, per lot after costs; Sensex and Midcap asserted), and Bank Nifty's
  TradingView rating (tv_ratings_study tv_ours, asserted; the rating's strong run is its signal).
  TODAY         enter at the close the signal appears (as the backtests always have)
  STRIKE_CLOSE  enter at the first 15-minute close, while the signal still holds, that is beyond that strike - above
                it for a call, below it for a put (at once if the signal's own close already is); if the signal ends
                first, no trade
Kept only if more than TODAY in BOTH the first two years and the held-out year, the rules' three indices pooled, then
on each; robustness.py's walk-forward and Monte Carlo line under each verdict. Also: how many signals were already
beyond the strike, how many waited and how long, how many never got there.
(The live tool enters on the FORMING candle, mid-candle; waiting for the plain 15-minute close was entry_timing_study's
"candle close" on 6 Oct 2026 - worse in the first two years, better held-out, dropped.)

RESULT (22 Oct 2026, history to 11 Sep 2026; strike_close_study_results.txt) - DROP, NOTHING BUILT. Profit against
today, first two years / held-out: the rules pooled -132,796 / -74,486 (walk-forward 2 of 7 half-years); Nifty -50,067
/ +328, Sensex -26,378 / -49,125, Midcap -56,350 / -25,689, Bank Nifty's TradingView rating -20,658 / -40,257. Half
the signals are already beyond the strike at their own close (nothing changes for them); about a fifth wait 1-2 closes
(15-30 minutes) and buy later, at a worse price; a quarter to a third never close through the strike and are skipped -
winners with them. Drawdown is smaller only because there are about 30% fewer trades.

    /usr/bin/python3 strike_close_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RULES = ("NIFTY", "SENSEX", "MIDCPNIFTY")
VARIANTS = ("TODAY", "STRIKE_CLOSE")


class Waiter:
    """The strike rule over a run of signal closes. call(i, buy, close) -> enter now? A run is consecutive closes that
    each carry a signal the same way; its strike is fixed at the run's first close."""

    def __init__(self, step):
        self.step, self.last_i, self.last_buy, self.k, self.start = step, None, None, None, None
        self.stats = {"runs": 0, "at_once": 0, "waited": 0, "wait_bars": 0}
        self._entered_run = False

    def __call__(self, i, buy, close):
        if not (self.last_i == i - 1 and self.last_buy == buy):
            self.stats["runs"] += 1
            self.k = round(close / self.step) * self.step
            self.start, self._entered_run = i, False
        self.last_i, self.last_buy = i, buy
        ok = close > self.k if buy else close < self.k
        if ok and not self._entered_run:
            self._entered_run = True
            if i == self.start:
                self.stats["at_once"] += 1
            else:
                self.stats["waited"] += 1
                self.stats["wait_bars"] += i - self.start
        return ok


def _rules_job(job):
    key, v = job
    import config
    import order_block_study as ob
    box = {}

    def make(df, pre):
        if v == "TODAY":
            return None, None, None
        w = box["w"] = Waiter(config.INSTRUMENTS[key]["strike_step"])
        cl = df["Close"].to_numpy()
        return (lambda i, buy: w(i, buy, float(cl[i]))), None, None
    rows = ob.rules_rows(key, make)
    return job, (rows, box["w"].stats if "w" in box else None)


def _tv_job(job):
    _, v = job
    import config
    import tv_ratings_study as tvs
    stats = None
    if v != "TODAY":
        real = tvs.tv_ratings
        w = Waiter(config.INSTRUMENTS["BANKNIFTY"]["strike_step"])
        stats = w.stats

        def filtered(df, real=real):
            rt = real(df).copy()
            cl = df["Close"].to_numpy()
            r = rt["all"].to_numpy(float).copy()
            for i in range(len(r)):
                if r[i] > tvs.STRONG or r[i] < -tvs.STRONG:
                    if not w(i, r[i] > 0, float(cl[i])):
                        r[i] = 0.0
            rt["all"] = r
            return rt
        tvs.tv_ratings = filtered
    _, out = tvs._tv_job("BANKNIFTY")
    return job, (out["tv_ours"], stats)


def main():
    import multiprocessing as mp
    import order_block_study as ob
    import stop_day_study as sds
    jobs = [(k, v) for k in RULES for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(_rules_job, jobs, chunksize=1))
        got.update(dict(pool.map(_tv_job, [("BANKNIFTY", v) for v in VARIANTS], chunksize=1)))
    stats = {k: s for k, (_, s) in got.items()}
    got = {k: rows for k, (rows, _) in got.items()}
    for k, figs in ob.B_FIGS.items():
        s = sds.split(got[(k, "TODAY")])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == figs, f"{k} TODAY must be room_fix_study's B {figs}"
    s = sds.split(got[("BANKNIFTY", "TODAY")])
    assert (round(s["is"]["total"]), round(s["oos"]["total"])) == ob.TV_FIGS, "Bank Nifty TODAY must be tv_ours"
    pool_ = lambda v: sorted([r for k in RULES for r in got[(k, v)]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" ENTER ONLY AFTER A 15-MINUTE CLOSE BEYOND THE STRIKE THE TOOL GAVE - each index on its live system, per lot after"
          " costs, ₹ (columns: trades, won, total, profit factor, worst drop, worst day)")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" RULES: NIFTY + SENSEX + MIDCAP (the verdict)")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in RULES + ("BANKNIFTY",):
        print(f" {k}" + ("  (TradingView rating)" if k == "BANKNIFTY" else ""))
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    print("\n THE SIGNALS UNDER STRIKE_CLOSE (every signal run, before the one-position and re-entry rules)")
    for k in RULES + ("BANKNIFTY",):
        st = stats.get((k, "STRIKE_CLOSE")) or {}
        n = st.get("runs") or 1
        never = n - st.get("at_once", 0) - st.get("waited", 0)
        print(f"   {k:11s} {st.get('runs', 0):>5} signals: already beyond the strike at once {st.get('at_once', 0) / n:5.1%},"
              f" waited and got there {st.get('waited', 0) / n:5.1%} (on average {st.get('wait_bars', 0) / max(1, st.get('waited', 0)):.1f}"
              f" closes, 15 min each), never got there {never / n:5.1%}")
    print("\n VERDICT - kept only if more than TODAY in BOTH periods (pooled), then on each index")
    print("  " + sds.verdict("STRIKE_CLOSE rules pooled", pool_("STRIKE_CLOSE"), pool_("TODAY")).strip())
    for k in RULES + ("BANKNIFTY",):
        print("      " + sds.verdict(f"STRIKE_CLOSE {k}", got[(k, "STRIKE_CLOSE")], got[(k, "TODAY")]).strip())
    return got


if __name__ == "__main__":
    main()
