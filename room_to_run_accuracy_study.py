#!/usr/bin/env python3
"""
room_to_run_accuracy_study.py — is the tool's room to run a good estimate of how far price really goes? (3 years)
================================================================================
The user, 14 Oct 2026: "can you analyse today when the market didnt go up where the tool showed it have room to run
and it came back is the room to run we are calculating is wrong".

Room to run (signal_engine.compute_reachability) is a CEILING on the move left today, not a forecast of direction: the
tightest of (1) the option market's expected move for the time left (the ATM straddle x sqrt(time left / time to
expiry)), (2) the heaviest call / put open-interest strike, (3) what is left of a normal day's range - the typical
day x a trend multiplier by ADX (x1.25 at 20, x1.6 at 25, x2 at 35+) minus what the day has used, floored at half the
expected move, and on a day that has used its whole normal range with price near its extreme, one more normal day in
that direction. T1 / T2 / T3 are 40 / 70 / 100% of it.

ON EVERY RULES ENTRY OF THE 3 YEARS (bt.run with the live gate, all four indices), price's real best move in the
trade's direction from the entry candle's close to the day's close - whatever the trade did - against each estimate:
  BACKTEST    the room the 3-year studies used: the range limit only, its "normal day" the mean of EVERY prior day
              since 2023 (backtest_intraday.precompute) - no option chain, no OI walls in the history
  LIVE-LIKE   as live minus the OI walls: the normal day the mean of the last 10 days (the live feed holds 15 calendar
              days of candles), the ADX multiplier and trend-day room, and the expected move from the index's own
              VIX-scaled volatility (0.8 x sigma x sqrt(time left): what an ATM straddle prices), the tighter of the two
  EXPECTED MOVE  the option market's move for the time left alone
  ADR LEFT    the classic "average daily range left": the 10-day normal day minus what is used, no ADX multiplier
Reported: how often price reached the full room and its first 40% (T1), the median of real move / room, and the rank
correlation of room with the real move (does a bigger room mean a bigger move?) - overall and by ADX at entry.
For a perfectly priced 0.8-sigma room and a coin-flip direction, a driftless walk touches it ~42% of the time and its
40% ~75%: those are the "accurate" marks.

RESULT (14 Oct 2026, history to 11 Sep 2026; room_to_run_accuracy_study_results.txt), 7,772 entries:
  BACKTEST    the full room reached on 7% of entries, its 40% (T1) on 30%; real move / room median 0.23; within one
              index its size says almost nothing about the real move (rank correlation 0.04-0.09). Every 3-year study
              measured the rules with THESE targets - about twice as far as live's.
  LIVE-LIKE   full room 18-20%, T1 54-57% (an accurate room: ~42% / ~75%); median 0.46 - about TWICE the real move;
              but informative (rank correlation 0.29-0.33 within an index). Its useful part is the expected move.
  EXPECTED MOVE  17-18% / 52-55%, median 0.44, correlation 0.30-0.34 - the best single signal of the move's size,
              about twice too big (a day's options price overnight gaps too, and price more movement than comes).
  ADR LEFT    35% / 65%, median 0.63 - nearer in size, but its size says little (correlation 0.10-0.16).
  The ADX stretch makes the backtest room worse the stronger the trend (T1 reached 39% under ADX 25, 24% at 35-50).
  Today (14 Oct) the losing calls were bought within minutes of the 09:45 top (best move 26-179 points before the
  turn): a direction miss the room cannot fix. Nothing changed live.

    python3 room_to_run_accuracy_study.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")


def _job(k):
    import numpy as np
    import pandas as pd
    import config
    import backtest_intraday as bt
    import regime_study as rs
    import reversal_exit_study as res
    df = bt.fetch_history(k, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, rs.load_vix(), nrv)["sigma"].to_numpy()
    pre = bt.precompute(df, k)
    hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    day = np.array([t.date() for t in df.index])
    full = df.groupby(day).agg(h=("High", "max"), l=("Low", "min"))
    rng10 = (full["h"] - full["l"]).rolling(10).mean().shift(1)          # the live feed's ~10 prior days
    typ10 = pd.Series(day).map(rng10).to_numpy(dtype=float)
    used = pre["used_today"].to_numpy(dtype=float)
    adx = pre["adx"].to_numpy(dtype=float)
    dhi, dlo = pre["day_high"].to_numpy(dtype=float), pre["day_low"].to_numpy(dtype=float)
    trades = bt.run(k, df, gate=lambda i, rec: res.live_gate(k, i, rec, df))["trades"]
    pos = {t: n for n, t in enumerate(df.index)}
    out = []
    for tr in trades:
        i = pos[tr["when"]]
        j = i
        while j + 1 < len(cl) and day[j + 1] == day[i]:
            j += 1
        if j == i or tr.get("t3") is None:
            continue
        ce = tr["side"] == "CE"
        e = tr["entry"]
        mfe = (hi[i + 1:j + 1].max() - e) if ce else (e - lo[i + 1:j + 1].min())
        close_t = df.index[i] + pd.Timedelta(minutes=15)
        h_left = max(0.0, (pd.Timestamp.combine(close_t.date(), pd.Timestamp("15:30").time()).tz_localize(close_t.tz)
                           - close_t).total_seconds() / 3600.0)
        s = sig[i]
        em = 0.8 * e * s * math.sqrt(h_left / (252 * 6.25)) if s == s and s > 0 and h_left > 0 else np.nan
        mult = config.range_expansion(adx[i])
        t10 = typ10[i]
        rng_room = max(0.0, t10 * mult - used[i]) if t10 == t10 else np.nan
        if rng_room == rng_room and em == em:
            rng_room = max(rng_room, 0.5 * em)
        if t10 == t10 and used[i] >= t10 * mult and dhi[i] > dlo[i]:            # trend-day room, the trade's way
            near = config.TREND_DAY_NEAR_EXTREME * (dhi[i] - dlo[i])
            if (ce and e >= dhi[i] - near) or (not ce and e <= dlo[i] + near):
                rng_room = max(rng_room, t10 * mult)
        live = np.nanmin([em, rng_room]) if (em == em or rng_room == rng_room) else np.nan
        adr = max(0.0, t10 - used[i]) if t10 == t10 else np.nan
        out.append({"when": tr["when"], "index": k, "side": tr["side"], "adx": adx[i], "mfe": mfe,
                    "BACKTEST": abs(tr["t3"] - e), "LIVE-LIKE": live, "EXPECTED MOVE": em, "ADR LEFT": adr})
    return k, out


def main():
    from concurrent.futures import ProcessPoolExecutor
    import numpy as np
    import pandas as pd
    import stop_day_study as sds
    with ProcessPoolExecutor(max_workers=4) as ex:
        got = dict(ex.map(_job, INDICES))
    d = pd.DataFrame([r for k in INDICES for r in got[k]])
    est = ("BACKTEST", "LIVE-LIKE", "EXPECTED MOVE", "ADR LEFT")

    def row(sub, name):
        x = sub[[name, "mfe"]].dropna()
        x = x[x[name] > 0]
        if not len(x):
            return f"{'':>6} -"
        ratio = x["mfe"] / x[name]
        rho = x[name].rank().corr(x["mfe"].rank())
        return (f"{len(x):>6} {100 * (ratio >= 1).mean():>5.0f}% {100 * (ratio >= 0.4).mean():>5.0f}%"
                f" {ratio.median():>6.2f} {rho:>6.2f}")
    hdr = f"{'':6} {'full':>6} {'40%':>6} {'move/':>6} {'rank':>6}\n {'':34s}{'trades':>6} {'room':>6} {'(T1)':>6} {'room':>6} {'corr':>6}"
    print("=" * 120)
    print(" ROOM TO RUN vs THE REAL MOVE - every rules entry, 3 years, all four indices: how often price reached the room"
          " in the trade's direction before the day's close")
    print("=" * 120)
    for period, sel in (("ALL 3 YEARS", d), ("FIRST TWO YEARS", d[d["when"] < sds.SPLIT]), ("HELD-OUT YEAR", d[d["when"] >= sds.SPLIT])):
        print(f"\n {period}" + "   (a perfectly priced room with a coin-flip direction: full ~42%, 40% ~75%)")
        print(" " + " " * 34 + hdr.split("\n")[0].strip().rjust(40))
        print(hdr.split("\n")[1])
        for name in est:
            print(f"   {name:31s}{row(sel, name)}")
    print("\n BY ADX AT ENTRY (all 3 years) - the room is stretched x1.25 at ADX 20, x1.6 at 25, x2 at 35+")
    for lo_, hi_, lab in ((0, 25, "ADX under 25"), (25, 35, "ADX 25-35 (x1.6)"), (35, 50, "ADX 35-50 (x2)"), (50, 999, "ADX 50+ (x2)")):
        sub = d[(d["adx"] >= lo_) & (d["adx"] < hi_)]
        print(f"   {lab}")
        for name in ("BACKTEST", "LIVE-LIKE", "ADR LEFT"):
            print(f"     {name:29s}{row(sub, name)}")
    print("\n BY INDEX (all 3 years) - the rank correlation within one index (the pooled one mixes point scales)")
    for k in INDICES:
        print(f"   {k}")
        for name in est:
            print(f"     {name:29s}{row(d[d['index'] == k], name)}")
    print(f"\n real best move in the trade's direction, median points: " +
          ", ".join(f"{k} {d[d['index'] == k]['mfe'].median():.0f}" for k in INDICES))
    return d


if __name__ == "__main__":
    main()
