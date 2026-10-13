#!/usr/bin/env python3
"""
kronos_day_study.py — the best exit for Kronos's whole-day option trade, on three years
================================================================================
The user, 13 Oct 2026, after the Kronos tab's whole-day trade went live and its stop (the lowest point of its own
forecast) turned out to sit a few points from the entry: "so whatever is best for option trading and exit on the
profits which ever is best i want to go ahead with that". This finds that exit on the real 3-year history, priced as
the option you would buy, and the Kronos tab then uses it.

kronos_forecast.py --day ran Kronos-small (the live worker's settings: 400 candles of context, temperature 1.0, top-p
0.9, five samples averaged) from every regular session's 09:15 candle to the 15:15 one. This replays each of those
days as the tab trades it: at the 09:15 candle's close, buy the at-the-money call (forecast up) or put (down), on the
nearest real expiry (Midcap Select monthly), priced Black-Scholes at that day's VIX-scaled volatility, 0.25%
slippage a side and Zerodha's charges, per lot. A candle that touches both the stop and the target counts as the
stop. Whatever is still open at the 15:15 candle's close is sold there.

FIXED BEFORE ANY RESULT WAS SEEN (13 Oct 2026):
  Entries   every day the forecast's move to the close is at least 0.15% of the price - the tab's "skip small
            calls" rule - the same days for every exit below, so the exits are compared on identical trades. The
            live rule as deployed also needs the target at least as far as its stop (row "live today").
  Exits     live today       Kronos's target (its forecast close) and its stop (its forecast's worst point), R:R >= 1
            K target+K stop  the same exit without the R:R filter
            hold             no stop, no target: out at the close
            K target         Kronos's target, no stop
            K target, 0.25R  Kronos's target, a stop 0.25 x the index's average day range (last 10 sessions)
            K target, 0.5R   ...0.5 x the average day range
            hold, 0.5R       no target, the 0.5 x day-range stop
            K target, 0.5R, then entry   Kronos's target, the 0.5 x range stop moved to the entry once the index is
                             halfway to the target ("exit on the profits")
            prem +30/-20     on the option's own price: out at +30%, stop at -20%
            prem +50/-30     ...+50% / -30%
            prem +100/-40    ...+100% / -40%
  ADDED 13 Oct 2026, 08:40 IST, before these were run (the user, after "hold to the close" was chosen: "target should
  not be at the close target should be before the market hits the top whatever ce or pe before that it should take
  the profit and come out", and "add a ladder like we have it in our tool from entry to target or to stop loss").
  P = Kronos's best point of the day (its highest forecast high for a call, lowest low for a put):
            to peak          target P, no stop
            75% to peak      target 75% of the way from the entry to P, no stop
            50% to peak      ...50%
            ladder           T1 50%, T2 75%, T3 P; at T1 the stop goes to the entry, at T2 to T1 (the tool's own
                             ratchet); out at T3
            peak time        out at P, or at the close of the candle Kronos expects P on, whichever comes first
            75% / 50% / ladder, each with the 0.5 x day-range stop as well
  Chosen among THESE (the user's "before the top") by the same measure - the larger total over both periods - and
  set beside "hold to the close" so what taking profit early costs or earns is plain.
  For information only (not a choice): the first candle's own direction, held to the close - what a day trade
  earns with no model at all.
  CHOSEN    the exit that makes more than "live today" in BOTH the first two years and the held-out final year (from
            15 Aug 2025 - after the model's weights were made, the only period it cannot have seen), all four indices
            pooled; of those, the one with the larger total over both. If none does, "live today" stays. Whether the
            chosen exit actually MAKES money in the held-out year is reported separately - better than today is not
            the same as profitable.

Limits: constant volatility through the day (no IV crush or spike), today's lot sizes, a fill exactly at the stop or
target level; the premium exits are checked on each candle's high and low priced at the candle's close time.

RESULT (13 Oct 2026, Nifty + Bank Nifty - Sensex and Midcap Select's forecasts were held for after the open, at the
user's word; full table in kronos_day_study_results.txt) - CHOSEN: HOLD TO THE CLOSE, now the tab's exit.
  * live today (its forecast's own target and stop, a few points away): -76,130 / +80,920 - stopped out on two
    trades in three in the first two years.
  * hold to the close: +409,486 / +213,349 (PF 1.41 / 1.33, worst drop 46,186 / 49,454) - the most in both periods;
    every exit with a stop or a target did less, and every exit on the option's own price did less in the final year
    than today's.
  * NOT all model: the first candle's own direction held to the close, no Kronos at all, made +400,192 / +147,619 on
    every day (PF 1.27 / 1.16, worst drop 121,117 / 94,692) - holding a day's option in a direction did well in these
    years on this pricing. Kronos's version did better in the final year on fewer days with half the drawdown, but on
    Bank Nifty's ordinary days it made less than the no-model version (+43,347 vs +75,891), and 10 Bank Nifty expiry
    days gave +61,417 of its final year - the days constant-volatility pricing is least like the real premium.
  * Profitable on ordinary (non-expiry) days on both indices, both periods: Nifty +199,720 / +76,919, Bank Nifty
    +73,883 / +43,347.

THEN, the same morning - the user's "before the top" exits (added before they were run, see above), Nifty + Bank Nifty:
  to peak +185,512 / +208,997 (the best of them over both periods); peak time +165,443 / +224,203; ladder 50/75/P with
  the ratchet +131,431 / +223,190; with the 0.5 x range stop: ladder +65,816 / +188,504, 75% +38,241 / +137,590, 50%
  +15,086 / +113,266. Every one below holding to the close in the first two years. The user then asked for a stop
  loss as well ("its not giving a stop loss"): the LADDER WITH THE STOP - the best of those with a stop - is the
  tab's exit (kronos_worker.DAY_EXIT = "ladder").

    python3 kronos_day_study.py          (after ~/kronos-test/out/<INDEX>_day.csv exist for all four)
    python3 kronos_day_study.py NIFTY BANKNIFTY      (only those - the user, 13 Oct 2026, before the open: "we can hold
                                                      on sensex and midcpnifty for today"; the same rule, pooled over
                                                      the indices named)
"""
import datetime as dt
import math
import os

import numpy as np
import pandas as pd

OUT = os.path.expanduser("~/kronos-test/out")
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")
NAMES = {"NIFTY": "Nifty", "BANKNIFTY": "Bank Nifty", "SENSEX": "Sensex", "MIDCPNIFTY": "Midcap Select"}
DAY_LEN = 24
MIN_MOVE_PCT = 0.15
RANGE_DAYS = 10
# (key, label, kind, params) - kind "index": levels on the index; "prem": levels on the option's own price
EXITS = (
    ("live", "live today (K target + K stop, R:R >= 1)", "index", {"target": "k", "stop": "k", "rr": True}),
    ("kk", "K target + K stop", "index", {"target": "k", "stop": "k"}),
    ("hold", "hold to the close", "index", {}),
    ("kt", "K target, no stop", "index", {"target": "k"}),
    ("kt25", "K target, stop 0.25 x day range", "index", {"target": "k", "stop": 0.25}),
    ("kt50", "K target, stop 0.5 x day range", "index", {"target": "k", "stop": 0.5}),
    ("hold50", "hold, stop 0.5 x day range", "index", {"stop": 0.5}),
    ("kt50be", "K target, 0.5 x range stop, then entry", "index", {"target": "k", "stop": 0.5, "be": True}),
    ("p3020", "premium +30% / -20%", "prem", {"up": 0.30, "down": 0.20}),
    ("p5030", "premium +50% / -30%", "prem", {"up": 0.50, "down": 0.30}),
    ("p10040", "premium +100% / -40%", "prem", {"up": 1.00, "down": 0.40}),
)
# the user's "take the profit before the top" (13 Oct 2026, added before they were run)
PEAK_EXITS = (
    ("pk100", "to peak", "peak", {"f": 1.0}),
    ("pk75", "75% to peak", "peak", {"f": 0.75}),
    ("pk50", "50% to peak", "peak", {"f": 0.5}),
    ("pkld", "ladder 50/75/peak, ratchet", "ladder", {}),
    ("pktm", "peak time", "peaktime", {}),
    ("pk75s", "75% to peak, stop 0.5 x range", "peak", {"f": 0.75, "stop": 0.5}),
    ("pk50s", "50% to peak, stop 0.5 x range", "peak", {"f": 0.5, "stop": 0.5}),
    ("pklds", "ladder, stop 0.5 x range", "ladder", {"stop": 0.5}),
)
INFO = (("first", "first candle's direction, hold (no model)"),)


def forecasts(key):
    """The whole-day forecasts: {09:15 candle time: (close, closes[24], highs[24], lows[24])}."""
    df = pd.read_csv(os.path.join(OUT, f"{key}_day.csv")).drop_duplicates("i", keep="last")
    out = {}
    for r in df.itertuples(index=False):
        d = r._asdict()
        out[pd.Timestamp(d["time"])] = (d["close"], [d[f"c{h}"] for h in range(1, DAY_LEN + 1)],
                                        [d[f"h{h}"] for h in range(1, DAY_LEN + 1)],
                                        [d[f"l{h}"] for h in range(1, DAY_LEN + 1)])
    return out


def kronos_levels(close, closes, highs, lows):
    """The tab's own trade from a forecast (kronos_worker.trade_from): side, target (its close at the end of the day)
    and stop (its worst point on the way; its target's distance the other way if that is not against it)."""
    end = closes[-1]
    if end == close:
        return None
    up = end > close
    stop = min(lows) if up else max(highs)
    if (stop >= close) if up else (stop <= close):
        stop = close - (end - close)
    return {"side": "CE" if up else "PE", "entry": close, "target": end, "stop": stop}


def walk_index(day, side, entry, target=None, stop=None, be_at=None):
    """Play a trade on the day's candles [(hi, lo, cl), ...] with levels on the index. Stop first within a candle.
    be_at: once the index reaches this level, the stop moves to the entry. -> (exit spot, candle number, how)."""
    up = side == "CE"
    for j, (hi, lo, cl) in enumerate(day):
        if stop is not None and ((lo <= stop) if up else (hi >= stop)):
            return stop, j, "stop"
        if target is not None and ((hi >= target) if up else (lo <= target)):
            return target, j, "target"
        if be_at is not None and ((hi >= be_at) if up else (lo <= be_at)):
            stop = entry if stop is None else (max(stop, entry) if up else min(stop, entry))
            be_at = None
    return day[-1][2], len(day) - 1, "close"


def walk_ladder(day, side, entry, t1, t2, t3, stop=None):
    """T1, T2, T3 on the way to the target, the tool's own ratchet: once T1 is touched the stop is the entry (or
    stays where it was if that is already better), once T2 is touched it is T1; out at T3, the stop, or the last
    close. Stop first within a candle. -> (exit spot, candle number, how)."""
    up = side == "CE"
    better = (lambda a, b: max(a, b)) if up else (lambda a, b: min(a, b))
    hit1 = hit2 = False
    for j, (hi, lo, cl) in enumerate(day):
        if stop is not None and ((lo <= stop) if up else (hi >= stop)):
            return stop, j, "stop"
        if (hi >= t3) if up else (lo <= t3):
            return t3, j, "target"
        if not hit1 and ((hi >= t1) if up else (lo <= t1)):
            hit1 = True
            stop = entry if stop is None else better(stop, entry)
        if not hit2 and ((hi >= t2) if up else (lo <= t2)):
            hit2 = True
            stop = better(stop, t1)
    return day[-1][2], len(day) - 1, "close"


def walk_premium(day, prem_at, p0, up_pct, down_pct):
    """Play a trade on the option's own price: prem_at(spot, j) is the premium at candle j's close time. The worst
    point of a candle is checked first (the stop), then the best (the target); else out at the last close.
    -> (exit premium, candle number, how)."""
    stop, tgt = p0 * (1 - down_pct), p0 * (1 + up_pct)
    for j, (hi, lo, cl) in enumerate(day):
        a, b = prem_at(hi, j), prem_at(lo, j)
        worst, best = min(a, b), max(a, b)
        if worst <= stop:
            return stop, j, "stop"
        if best >= tgt:
            return tgt, j, "target"
    return prem_at(day[-1][2], len(day) - 1), len(day) - 1, "close"


def net_rupees(key, p_buy, p_sell, qty):
    """Per lot, after 0.25% slippage a side and Zerodha's charges (pro_study.price's own arithmetic)."""
    import config
    import regime_study as rs
    exch = "BSE" if config.INSTRUMENTS[key]["kite_exchange"] == "BSE" else "NSE"
    buy = p_buy * (1 + rs.SLIP) * qty
    sell = max(p_sell * (1 - rs.SLIP), 0.0) * qty
    brokerage = rs.BROKERAGE_PER_ORDER * 2
    stt, txn = rs.STT_SELL * sell, rs.TXN[exch] * (buy + sell)
    sebi, stamp = rs.SEBI_PER_RUPEE * (buy + sell), rs.STAMP_BUY * buy
    gst = rs.GST * (brokerage + txn + sebi)
    return sell - buy - (brokerage + stt + txn + sebi + stamp + gst)


def replay(key):
    """{exit key: [trade rows]} for one index - every exit on the same days."""
    import config
    import backtest_intraday as bt
    import index_candidates_study as ics
    import pro_study as ps
    import regime_study as rs
    if key == "MIDCPNIFTY":
        real = ps._scheduled
        ps._scheduled = lambda k, d, real=real: ics._monthly(d) if k == key else real(k, d)
    meta = config.INSTRUMENTS[key]
    step, qty = meta["strike_step"], meta["lot_size"]
    df = bt.fetch_history(key, years=3, use_cache=True)
    nifty = bt.fetch_history("NIFTY", years=3, use_cache=True)
    vix = rs.load_vix()
    nrv = np.log(rs.daily_close(nifty)).diff().rolling(20).std() * math.sqrt(252)
    sig = rs.features(df, vix, nrv)["sigma"].to_numpy()
    days_set = set(df.index.date)
    hi, lo, cl = df["High"].to_numpy(), df["Low"].to_numpy(), df["Close"].to_numpy()
    dates = np.array(df.index.date)
    rng = df.groupby(dates).apply(lambda g: g["High"].max() - g["Low"].min())
    adr = rng.shift(1).rolling(RANGE_DAYS).mean()             # the average of the 10 sessions BEFORE this one
    pos = {t: n for n, t in enumerate(df.index)}
    fcs = forecasts(key)
    out = {k: [] for k, *_ in EXITS + PEAK_EXITS}
    out.update({k: [] for k, _ in INFO})
    for t, (close, closes, highs, lows) in sorted(fcs.items()):
        i = pos.get(t)
        if i is None or abs(cl[i] - close) > 1e-6 * max(1.0, close):
            continue                                          # the forecast must be of THIS candle
        js = list(range(i + 1, i + 1 + DAY_LEN))
        if js[-1] >= len(cl) or any(dates[j] != dates[i] for j in js):
            continue
        s, a = sig[i], adr.get(dates[i], np.nan)
        if not (s == s) or s <= 0 or not (a == a) or a <= 0:
            continue
        day = [(hi[j], lo[j], cl[j]) for j in js]
        when = df.index[i] + pd.Timedelta(minutes=15)
        exp = ps.expiry_on_or_after(key, when.date(), days_set)
        close_at = [df.index[j] + pd.Timedelta(minutes=15) for j in js]

        def row(side, exit_spot=None, j=None, how=None, p_exit=None):
            call = side == "CE"
            K = round(close / step) * step
            p0 = rs.bs(close, K, ps.years_to(exp, when), s, call)
            if p0 <= 0.5:
                return None
            if p_exit is None:
                p_exit = rs.bs(exit_spot, K, ps.years_to(exp, close_at[j]), s, call)
            return {"when": when, "index": key, "side": side, "how": how, "net": net_rupees(key, p0, p_exit, qty),
                    "expiry_day": exp == when.date()}

        # for information: no model - the first candle's own direction, held to the close
        first = "CE" if cl[i] >= df["Open"].iloc[i] else "PE"
        r = row(first, *walk_index(day, first, close))
        if r:
            out["first"].append(r)
        k = kronos_levels(close, closes, highs, lows)
        if not k or abs(k["target"] - close) / close * 100 < MIN_MOVE_PCT:
            continue
        side, up = k["side"], k["side"] == "CE"
        sgn = 1 if up else -1
        for key_, label, kind, p in EXITS:
            if kind == "prem":
                call = up
                K = round(close / step) * step
                p0 = rs.bs(close, K, ps.years_to(exp, when), s, call)
                if p0 <= 0.5:
                    continue
                prem_at = lambda x, j, K=K, call=call: rs.bs(x, K, ps.years_to(exp, close_at[j]), s, call)
                px, j, how = walk_premium(day, prem_at, p0, p["up"], p["down"])
                r = row(side, j=j, how=how, p_exit=px)
            else:
                if p.get("rr") and abs(k["target"] - close) < abs(k["stop"] - close):
                    continue
                target = k["target"] if p.get("target") == "k" else None
                st = p.get("stop")
                stop = k["stop"] if st == "k" else (close - sgn * st * a if st else None)
                be = close + 0.5 * (k["target"] - close) if p.get("be") else None
                r = row(side, *walk_index(day, side, close, target, stop, be))
            if r:
                out[key_].append(r)
        # the user's "before the top": P, Kronos's best point of the day, and the candle it expects it on
        best = max(range(DAY_LEN), key=lambda h: highs[h]) if up else min(range(DAY_LEN), key=lambda h: lows[h])
        P = highs[best] if up else lows[best]
        for key_, label, kind, p in PEAK_EXITS:
            st = p.get("stop")
            stop = close - sgn * st * a if st else None
            if kind == "peak":
                r = row(side, *walk_index(day, side, close, close + p["f"] * (P - close), stop))
            elif kind == "ladder":
                r = row(side, *walk_ladder(day, side, close, close + 0.5 * (P - close), close + 0.75 * (P - close), P,
                                           stop))
            else:                                         # peak time: P, or out when its candle closes
                spot, j, how = walk_index(day[:best + 1], side, close, P, stop)
                r = row(side, spot, j, how if how != "close" else "time")
            if r:
                out[key_].append(r)
    return key, out


def main(indices=INDICES):
    from concurrent.futures import ProcessPoolExecutor
    import stop_day_study as sds
    for k in indices:
        assert os.path.exists(os.path.join(OUT, f"{k}_day.csv")), f"run kronos_forecast.py --index {k} --day first"
    with ProcessPoolExecutor(max_workers=4) as ex:
        got = dict(ex.map(replay, indices))
    pooled = {e: [r for k in indices for r in got[k][e]] for e in [x[0] for x in EXITS + PEAK_EXITS] + [x[0] for x in INFO]}

    print("=" * 172)
    print(" KRONOS'S WHOLE-DAY OPTION TRADE - which exit, on three years. Per lot, after costs. columns: trades, won, "
          "total, profit factor, worst drop, worst single DAY")
    print("=" * 172)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" " + " + ".join(NAMES[k] for k in indices).upper())
    for e, label, *_ in EXITS:
        print(sds.line("   " + label, pooled[e]))
    for e, label in INFO:
        print(sds.line("   (info) " + label, pooled[e]))
    print(" THE USER'S \"TAKE THE PROFIT BEFORE THE TOP\" (P = Kronos's best point of the day)")
    for e, label, *_ in PEAK_EXITS:
        print(sds.line("   " + label, pooled[e]))
    for k in indices:
        print("-" * 172)
        print(f" {NAMES[k]}")
        for e, label, *_ in EXITS:
            print(sds.line("   " + label, got[k][e]))

    base = sds.split(pooled["live"])
    rows = []
    for e, label, *_ in EXITS[1:]:
        r = sds.split(pooled[e])
        di, do = r["is"]["total"] - base["is"]["total"], r["oos"]["total"] - base["oos"]["total"]
        rows.append((e, label, di, do, r["is"]["total"] + r["oos"]["total"], r["oos"]["total"]))
    keep = [x for x in rows if x[2] > 0 and x[3] > 0]
    print("\n VERDICT - an exit is chosen only if it beats 'live today' in BOTH periods; of those, the larger total")
    for e, label, di, do, tot, oos in rows:
        print(f"   {'beats' if di > 0 and do > 0 else '     '}  {label:44s} vs live: first two years {di:>+10,.0f}"
              f"   held-out {do:>+10,.0f}   total both {tot:>+10,.0f}   held-out alone {oos:>+10,.0f}")
    if keep:
        best = max(keep, key=lambda x: x[4])
        print(f"\n CHOSEN: {best[1]}  (held-out year {'MAKES' if best[5] > 0 else 'LOSES'} money: {best[5]:+,.0f})")
    else:
        print("\n CHOSEN: none beats 'live today' in both periods - the live exit stays")

    hold = sds.split(pooled["hold"])
    print("\n THE USER'S \"BEFORE THE TOP\" - the best of these by total over both periods, against hold to the close")
    pk = []
    for e, label, *_ in PEAK_EXITS:
        r = sds.split(pooled[e])
        pk.append((r["is"]["total"] + r["oos"]["total"], label, r))
    for tot, label, r in sorted(pk, key=lambda x: x[0], reverse=True):
        print(f"   {label:40s} both {tot:>+10,.0f}   first two years {r['is']['total']:>+10,.0f}   held-out "
              f"{r['oos']['total']:>+10,.0f}   vs hold: {r['is']['total'] - hold['is']['total']:>+10,.0f} / "
              f"{r['oos']['total'] - hold['oos']['total']:>+10,.0f}")
    print(f"\n BEST BEFORE THE TOP: {max(pk, key=lambda x: x[0])[1]}")
    return got


if __name__ == "__main__":
    import sys
    main(tuple(a.upper() for a in sys.argv[1:]) or INDICES)
