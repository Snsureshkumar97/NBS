#!/usr/bin/env python3
"""
trend_rider_rules_stop_study.py — the Trend Rider with the rules' own stop steps (Nifty + Bank Nifty, 3 years)
================================================================================
The user, 13 Oct 2026, after the rules' stop steps were explained: "can we do the same way for trend rider also as
the tools one is".

The Trend Rider today (config.TREND_RIDER, NEAR_TARGET_STEPS): one target at 2.75 x the risk, its T1 / T2 / T3 a third,
two thirds and all of it; 90% of the way to T2 the stop moves to T1, 90% of the way to T3 to T2; out at T3, the stop or
15:15. The rules (Sensex, Midcap): T1 touched -> the stop to T1, T2 touched -> to T2, each once that 15-minute candle has
closed (config.STEP_STOP_AT_CANDLE_CLOSE, since this evening), the Supertrend trail after T1, the 2-hour breakeven.
On 8 Oct trend_rider_t1_study.py dropped a T1 step for the Trend Rider - but moved on the touch, not at the candle close.

FIXED BEFORE ANY RESULT WAS SEEN (13 Oct 2026), Nifty + Bank Nifty on the 5-minute candles from the 15-minute entry,
priced as every Trend Rider study (vwap_ema_atr_study.price_trade), per lot after costs:
    TODAY  the near-target ladder as it runs (asserted to reproduce the live figures: this file's own walk in "next
           5-minute candle" mode must equal trend_rider_t1_study's, rupee for rupee)
    A      the rules' steps: T1 touched -> stop to T1, T2 touched -> stop to T2, each from the next 15-MINUTE candle
    B      A, plus the Supertrend trail once T1 has stepped and the 2-hour breakeven - the rules' whole exit
    C      (information) A with the steps from the next 5-minute candle - near "on the touch", as 8 Oct tested it
VERDICT: kept only if more than TODAY in BOTH the first two years and the held-out final year, the two indices pooled.

RESULT (13 Oct 2026, history to 11 Sep 2026; trend_rider_rules_stop_study_results.txt) - DROP, the Trend Rider keeps
its own ladder. Nifty + Bank Nifty, first two years / held-out: TODAY +339,081 / +115,033; A (the rules' steps at the
candle close) +316,807 / +51,360 (-22,274 / -63,672; Bank Nifty's held-out year a loss); B (+ trail + 2h breakeven)
+275,631 / +121,022 (-63,450 / +5,989 - about the same held-out profit with a much smaller worst drop, 63,489 -> 42,588,
and worst day, -32,218 -> -13,522, but far less in the first two years); C (the steps on the touch) the worst,
-54,540 / -83,643 - the candle close beats the touch here too. A step at T1 itself cuts the Trend Rider's runners:
it wins more often (45-49%) and earns less, as on 8 Oct.

    python3 trend_rider_rules_stop_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TREND = ("NIFTY", "BANKNIFTY")
TODAY_FIGS = {"NIFTY": (179191, 81254), "BANKNIFTY": (159890, 33779)}


def walk(bars, t5, start, side, entry, stop, risk, rr, steps, step15, trail, be, st_at, cutoff, t_in):
    """trend_rider_t1_study.walk's own loop (the stop as it stood first, a gap filled at the open, out by the cutoff),
    with: steps [(trigger R, stop-to R)] taking effect from the next 5-minute candle (step15 False - exactly that
    study's) or the next 15-minute candle (step15 True); trail - the Supertrend once the first step has taken effect;
    be - the 2-hour breakeven while no step has been triggered. -> (exit price, exit candle, how)."""
    import pandas as pd
    o, h, l, c, day = bars
    ce = side == "CE"
    tighten = (lambda a, b: max(a, b)) if ce else (lambda a, b: min(a, b))
    tgt = entry + rr * risk if ce else entry - rr * risk
    lv = lambda r: entry + r * risk if ce else entry - r * risk
    done = [False] * len(steps)
    pend = []                                     # (effective time, level)
    first_live = touched = be_done = False
    be_at = t_in + pd.Timedelta(minutes=120)
    d0 = day[start]
    j = start
    for j in range(start, len(c)):
        if day[j] != d0:
            return c[j - 1], j - 1, "square-off"
        for item in [x for x in pend if x[0] <= t5[j]]:
            stop = tighten(stop, item[1])
            first_live = True
            pend.remove(item)
        if (l[j] <= stop) if ce else (h[j] >= stop):
            return (min(o[j], stop) if ce else max(o[j], stop)), j, "stop"
        if (h[j] >= tgt) if ce else (l[j] <= tgt):
            return (max(o[j], tgt) if ce else min(o[j], tgt)), j, "target"
        for n, (trig_r, to_r) in enumerate(steps):
            trig = lv(trig_r)
            if done[n] or not ((h[j] >= trig) if ce else (l[j] <= trig)):
                continue
            done[n] = True
            touched = True
            if step15:
                pend.append((t5[j].floor("15min") + pd.Timedelta(minutes=15), lv(to_r)))
            else:
                stop = tighten(stop, lv(to_r))
                first_live = True
        if trail and first_live:
            s = st_at(j)
            if s == s:
                stop = tighten(stop, s)
        end = t5[j] + pd.Timedelta(minutes=5)
        if be and not touched and not be_done and end >= be_at and end.minute % 15 == 0:
            be_done = True
            if (c[j] <= entry) if ce else (c[j] >= entry):
                return c[j], j, "breakeven-close"
            stop = tighten(stop, entry)
        if cutoff[j]:
            return c[j], j, "square-off"
    return c[j], j, "square-off"


def _job(k):
    import datetime as dt
    import numpy as np
    import pandas as pd
    import config
    import entry_timing_study as ets
    import indicators as ind
    import stop_day_study as sds
    import trend_rider_t1_study as trs
    import vwap_ema_atr_study as vea
    RR = trs.RR
    c_ = sds._context()
    df = c_["hists"][k][["Open", "High", "Low", "Close", "Volume"]]
    days = set(df.index.date)
    sig = c_["F"][k]["sigma"]
    sigma_by_day = sig.groupby(sig.index.date).first().to_dict()
    st15 = ind.supertrend(df, *config.supertrend_params(k))[0].to_numpy()
    d5 = ets.load_5m(k)
    t5 = d5.index
    b5 = (d5["Open"].to_numpy(), d5["High"].to_numpy(), d5["Low"].to_numpy(), d5["Close"].to_numpy(),
          np.array([t.date() for t in t5]))
    cut5 = np.array([t.time() >= (dt.datetime.combine(dt.date(2000, 1, 1), trs.OUT_BY) - dt.timedelta(minutes=5)).time()
                     for t in t5])
    pos15 = df.index.searchsorted(t5 - trs.BAR15, side="right") - 1
    st_at = lambda p: st15[pos15[p]] if pos15[p] >= 0 else np.nan
    ladder = [(RR * 2 / 3 * 0.9, RR / 3), (RR * 0.9, RR * 2 / 3)]
    rules_steps = [(RR / 3, RR / 3), (RR * 2 / 3, RR * 2 / 3)]
    variants = {"TODAY": (ladder, False, False, False), "A": (rules_steps, True, False, False),
                "B": (rules_steps, True, True, True), "C": (rules_steps, False, False, False)}
    ent = trs.entries(df)
    out = {}
    for v, (steps, step15, trail, be) in variants.items():
        rows, free = [], -1
        for i, side, entry, stop in ent:
            if i <= free:
                continue
            risk = entry - stop if side == "CE" else stop - entry
            t_in = df.index[i] + trs.BAR15
            p0 = int(t5.searchsorted(t_in))
            if p0 >= len(t5) or t5[p0].date() != t_in.date():
                continue
            px, p, via = walk(b5, t5, p0, side, entry, stop, risk, RR, steps, step15, trail, be, st_at, cut5, t_in)
            t_out = t5[p] + pd.Timedelta(minutes=5)
            free_next = int(df.index.searchsorted(t5[p], side="right")) - 1
            s = sigma_by_day.get(t_in.date())
            net = vea.price_trade(k, days, t_in, entry, t_out, px, s, side == "CE") if s and s == s else None
            if net is not None:
                rows.append({"when": t_in, "exit_time": t_out, "net": net, "side": side, "closed_via": via, "index": k})
            free = free_next
        out[v] = rows
    return k, out


def main():
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=2) as ex:
        got = dict(ex.map(_job, TREND))
    for k, (a, b) in TODAY_FIGS.items():
        s = sds.split(got[k]["TODAY"])
        assert (round(s["is"]["total"]), round(s["oos"]["total"])) == (a, b), \
            f"{k}: this walk's TODAY must equal the live Trend Rider's figures, got {s['is']['total']:.0f} / {s['oos']['total']:.0f}"
    label = {"TODAY": "TODAY: 90% to T2 -> T1, 90% to T3 -> T2",
             "A": "A: rules' steps at the 15-min candle close",
             "B": "B: A + Supertrend trail + 2h breakeven",
             "C": "C: rules' steps on the touch (info)"}
    pool = lambda v: sorted([r for k in TREND for r in got[k][v]], key=lambda r: r["when"])
    W = 172
    print("=" * W)
    print(" THE TREND RIDER WITH THE RULES' STOP STEPS - Nifty + Bank Nifty, 5-minute candles. Per lot, after costs."
          "  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" NIFTY + BANK NIFTY")
    for v in ("TODAY", "A", "B", "C"):
        print(sds.line("   " + label[v], pool(v)))
    for k in TREND:
        print(f" {k}")
        for v in ("TODAY", "A", "B", "C"):
            print(sds.line("   " + label[v], got[k][v]))
    print("\n VERDICT - kept only if more than TODAY in BOTH periods")
    for v in ("A", "B", "C"):
        print("  " + sds.verdict(label[v], pool(v), pool("TODAY")).strip())
    return got


if __name__ == "__main__":
    main()
