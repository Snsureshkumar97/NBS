#!/usr/bin/env python3
"""
three_minute_gap_study.py — github.com/arshadakl/intraday-trading-bot's "3-Minute Breakout Strategy", six years
================================================================================
The user, 29 Oct 2026: "https://github.com/arshadakl/intraday-trading-bot.git can you backtest this startegy".
A Nifty 50 STOCK bot (Angel One, intraday cash, long and short) - not index options. Its README badge says "Expected
Win Rate 70%", from a "Simulated" table; it has no real backtest. Read at cd5c98d (25 Feb 2026), never run.

FIXED BEFORE ANY RESULT WAS SEEN (29 Oct 2026). Its rules (STRATEGY_RULES.md, src/strategy/three_minute_strategy.py,
src/analysis/daily_watchlist.py, src/analysis/stock_pickers/preopen_gap_picker.py, config/settings.json):
  the Nifty 50 index's open against yesterday's close: above +0.2% GAP UP, below -0.2% GAP DOWN, else FLAT;
  the Nifty 50 stocks ranked by their own opening gap (the pre-open price = the 09:15 open);
  GAP UP: SHORT the top 2 gappers; GAP DOWN: BUY the bottom 2; FLAT: buy the bottom 1 and short the top 1;
  the reference candle is 09:15-09:18 (plus the first tick after it - the bot feeds that tick in too);
  enter on the first 3-minute CLOSE beyond it (above its high to buy, below its low to short) - a wick does not count;
  no entry from 15:00; stop at the reference candle's far side, or 1% from the entry if the candle is wider than 1%;
  target 1% from the entry; out at 15:15; one trade a stock a day.
  AS SHIPPED the code also drops any pick whose own gap is under 1% (or over 10%) the trade's way: the picker's
  min_gap_percent is never set, so it is 1.0, and the bot keeps only the picks the picker also passed. The rules
  document does not say so - "AS DOCUMENTED" leaves that out.
Honest fills: the order goes in when the breakout candle closes - at the next minute's open (the stop and target are
the ones the signal set, off the candle's close, as the bot does); a stop or target touched inside a minute fills at
its level, a minute that OPENS beyond it fills at that open (fills.py's rule); a minute that touches both counts as
the stop. Booked per Rs 1,00,000 of stock a trade (about Rs 20,000 of intraday margin) after Zerodha's equity
intraday charges (brokerage min(20, 0.03%) an order, STT 0.025% of the sell, NSE 0.00297% and SEBI 10/crore of the
turnover, stamp 0.003% of the buy, GST 18% on brokerage + exchange + SEBI) and SLIPPAGE 0.02% of the price each way
(and again at 0.05%). Also shown: what its own paper trading would book (filled at the candle's close, no slippage).
CONTROLS on the same stock-days: the COIN (a coin for each stock's side, the rest the same; 20 coins) and the MIRROR
(the other side - gap up: buy the top gappers on a close above the reference high), shown for reference only.
WORKS only if it makes money in BOTH periods (to 15 Aug 2025, then held out), beats the coins, and holds up on
robustness.py's walk-forward / Monte Carlo. Caveat: today's 49 Nifty 50 names throughout (names that left the index
since 2020 are missing, names that joined late are in from the start).

RESULT (29 Oct 2026; three_minute_gap_study_results.txt). 3 Aug 2020 - 29 Oct 2026, 49 stocks, 1,550 days. Per Rs 1
lakh a trade, to 15 Aug 2025 / held out: it LOSES in both periods, whichever version.
  AS SHIPPED       1,259 trades 52% won -36,019 PF 0.93 | 224 trades 48% won -15,680 PF 0.83  (walk-forward 5 of 13)
  AS DOCUMENTED    1,679 trades 51% won -42,814 PF 0.94 | 327 trades 46% won -33,165 PF 0.75
  0.05% slippage   -111,033 / -28,976;  its own paper trading (candle-close fill, no slippage) +12,257 / -7,455
  MIRROR           -75,549 / -10,313;   COINS (average of 20) -57,506 / -13,613 - as shipped beats 17 of 20, then 9
  Win rate 51-52%, not the README's 70%. Before costs it makes +0.087% a trade; costs are about 0.12% (charges ~0.08%,
  slippage 0.04%), so -0.035% a trade. The coins make nearly as much before costs: a close out of the first 3-minute
  candle tends to run a little whichever way it breaks, and the gap logic adds only about Rs 17 a trade to that before
  15 Aug 2025 and nothing after. Exits: target 41%, stop 34%, out at 15:15 24%; 70% of trades had a reference candle
  wider than 1% (the biggest gappers open wild), so most are a 1% stop against a 1% target. By year: 2020 +7.0k,
  2021 +8.6k, then a loss every year 2022-2026. By gap: gap-up shorts +13,179 / -3,472, gap-down buys -31,918 /
  -8,228, flat -17,279 / -3,981 - no part of it holds in the held-out year.

    /usr/bin/python3 three_minute_gap_study.py DATA_DIR      (<SYM>_1m.csv.gz + <SYM>_day.csv, scratchpad fetch_min1.py;
                                                              DATA_DIR/topup/<SYM>_<DATE>.csv for days it missed)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
GAP_THR, LARGE_PCT, SL_PCT, TG_PCT, MAX_TRADES, PER_SIDE = 0.2, 1.0, 1.0, 1.0, 2, 4
MIN_GAP, MAX_GAP = 1.0, 10.0                  # the picker's filter, as shipped
REF_START, FIRST, NO_NEW, SQUARE = 555, 558, 900, 915      # minutes of the day: 09:15, 09:18, 15:00, 15:15
NOTIONAL, SLIP = 100000.0, 0.0002
BAD_GAP = 20.0                                # a gap this big is a corporate action or bad data: left out


def picks(gaps, nifty_gap, shipped=True):
    """(kind, [(sym, gap, side)]) - the day's stocks, as daily_watchlist.filter_watchlist_at_market_open picks them,
    and (shipped) only those the pre-open picker also passed."""
    s = sorted(gaps.items(), key=lambda kv: kv[1], reverse=True)
    top, bottom = s[:PER_SIDE], sorted(s[-PER_SIDE:], key=lambda kv: kv[1])
    if nifty_gap > GAP_THR:
        kind, chosen = "GAP_UP", [(k, g, "SHORT") for k, g in top[:MAX_TRADES]]
    elif nifty_gap < -GAP_THR:
        kind, chosen = "GAP_DOWN", [(k, g, "LONG") for k, g in bottom[:MAX_TRADES]]
    else:
        n = max(1, MAX_TRADES // 2)
        kind, chosen = "FLAT", [(k, g, "LONG") for k, g in bottom[:n]] + [(k, g, "SHORT") for k, g in top[:n]]
    if shipped:
        chosen = [x for x in chosen if (MIN_GAP <= x[1] <= MAX_GAP if x[2] == "SHORT" else -MAX_GAP <= x[1] <= -MIN_GAP)]
    return kind, chosen


def simulate(m, o, h, l, c, side, fill="next"):
    """One stock's day of 1-minute candles (m = minute of the day). The trade, or None."""
    ref = (m >= REF_START) & (m < FIRST)
    after = np.nonzero(m >= FIRST)[0]
    if not ref.any() or not len(after):
        return None
    j0 = after[0]
    rh, rl = max(h[ref].max(), o[j0]), min(l[ref].min(), o[j0])
    large = (rh - rl) / rl * 100 > LARGE_PCT
    cur_b = cur_close = None
    for j in range(j0, len(m)):
        b = (m[j] - FIRST) // 3
        if cur_b is not None and b > cur_b:            # the 3-minute candle cur_b has closed; seen at this minute
            if m[j] >= NO_NEW:
                return None
            if (side == "LONG" and cur_close > rh) or (side == "SHORT" and cur_close < rl):
                return _trade(m, o, h, l, c, j, cur_close, side, large, rh, rl, fill)
        cur_b, cur_close = b, c[j]
    return None


def _trade(m, o, h, l, c, j, signal, side, large, rh, rl, fill):
    up = side == "LONG"
    if up:
        sl = signal * (1 - SL_PCT / 100) if large else rl
        tg = signal * (1 + TG_PCT / 100)
    else:
        sl = signal * (1 + SL_PCT / 100) if large else rh
        tg = signal * (1 - TG_PCT / 100)
    sl, tg = round(sl, 2), round(tg, 2)
    entry = o[j] if fill == "next" else signal
    exit_, how, both = c[-1], "CLOSE", False
    for k in range(j, len(m)):
        if m[k] >= SQUARE:
            exit_, how = o[k], "SQUARE_OFF"
            break
        op = o[k]
        if (op <= sl) if up else (op >= sl):
            exit_, how = op, "STOP"
            break
        if (op >= tg) if up else (op <= tg):
            exit_, how = op, "TARGET"
            break
        hit_sl = l[k] <= sl if up else h[k] >= sl
        hit_tg = h[k] >= tg if up else l[k] <= tg
        if hit_sl:
            exit_, how, both = sl, "STOP", hit_tg
            break
        if hit_tg:
            exit_, how = tg, "TARGET"
            break
    return {"i": j, "side": side, "entry": float(entry), "exit": float(exit_), "how": how, "both": bool(both),
            "large": bool(large), "sl": sl, "tg": tg}


def charges(entry, exit_, qty, side, slip):
    """Zerodha equity intraday, in and out, plus slippage both ways - rupees."""
    buy, sell = (entry * qty, exit_ * qty) if side == "LONG" else (exit_ * qty, entry * qty)
    brokerage = min(20.0, 0.0003 * buy) + min(20.0, 0.0003 * sell)
    stt = 0.00025 * sell
    txn = 0.0000297 * (buy + sell)
    sebi = 1e-6 * (buy + sell)
    stamp = 0.00003 * buy
    gst = 0.18 * (brokerage + txn + sebi)
    return brokerage + stt + txn + sebi + stamp + gst + slip * (buy + sell)


def book(t, slip=SLIP):
    qty = max(1, int(NOTIONAL // t["entry"]))
    gross = (t["exit"] - t["entry"]) * qty * (1 if t["side"] == "LONG" else -1)
    net = gross - charges(t["entry"], t["exit"], qty, t["side"], slip)
    return dict(t, gross=gross, net=net, pct=100 * net / (qty * t["entry"]))


def _minutes(path):
    df = pd.read_csv(path, usecols=["Date", "Open", "High", "Low", "Close"])
    top = os.path.join(os.path.dirname(path), "topup")              # days the 60-day fetch missed, one file each
    sym = os.path.basename(path)[:-len("_1m.csv.gz")]
    extra = [os.path.join(top, f) for f in (os.listdir(top) if os.path.isdir(top) else [])
             if f.rsplit("_", 1)[0] == sym]
    if extra:
        df = pd.concat([df] + [pd.read_csv(f, usecols=["Date", "Open", "High", "Low", "Close"]) for f in extra])
        df = df.drop_duplicates("Date", keep="last").sort_values("Date", kind="stable").reset_index(drop=True)
    ts = pd.to_datetime(df["Date"].str[:19], format="%Y-%m-%d %H:%M:%S")
    ns = ts.to_numpy().astype("datetime64[m]").astype(np.int64)
    day = ns // 1440
    return {"day": day, "m": (ns % 1440).astype(np.int64), "o": df["Open"].to_numpy(), "h": df["High"].to_numpy(),
            "l": df["Low"].to_numpy(), "c": df["Close"].to_numpy(), "ts": ts}


def _days(path):
    df = pd.read_csv(path, usecols=["Date", "Open", "Close"])
    df.index = pd.to_datetime(df["Date"].str[:10]).dt.date
    return df[["Open", "Close"]]


def main():
    import stop_day_study as sds
    data = sys.argv[1]
    syms = sorted(f[:-8] for f in os.listdir(data) if f.endswith("_day.csv") and f != "NIFTY50_day.csv")
    nifty = _days(os.path.join(data, "NIFTY50_day.csv"))
    days = {s: _days(os.path.join(data, f"{s}_day.csv")) for s in syms}
    want, kinds, bad = {}, {}, 0                      # (sym, date) -> {variant: side}
    dates = list(nifty.index)
    for d0, d in zip(dates, dates[1:]):
        ng = (nifty.at[d, "Open"] - nifty.at[d0, "Close"]) / nifty.at[d0, "Close"] * 100
        gaps = {}
        for s in syms:
            df = days[s]
            if d in df.index and d0 in df.index:
                g = (df.at[d, "Open"] - df.at[d0, "Close"]) / df.at[d0, "Close"] * 100
                if abs(g) > BAD_GAP:
                    bad += 1
                    continue
                gaps[s] = g
        if len(gaps) < 2 * PER_SIDE:
            continue
        for variant, shipped in (("shipped", True), ("documented", False)):
            kind, chosen = picks(gaps, ng, shipped)
            kinds[d] = kind
            for s, g, side in chosen:
                want.setdefault((s, d), {})[variant] = side
    print(f"{len(dates)} days, {bad} stock-days left out (gap over {BAD_GAP:.0f}%: a corporate action or bad data)")
    rng = np.random.default_rng(29102026)
    coins = {key: rng.random(20) < 0.5 for key, v in want.items() if "shipped" in v}
    rows = {k: [] for k in ("shipped", "documented", "paper", "mirror")}
    coin_rows = [[] for _ in range(20)]
    epoch = pd.Timestamp("1970-01-01").date()
    for s in syms:
        need = sorted({d for (x, d) in want if x == s})
        if not need:
            continue
        A = _minutes(os.path.join(data, f"{s}_1m.csv.gz"))
        for d in need:
            code = (d - epoch).days
            a, b = np.searchsorted(A["day"], code), np.searchsorted(A["day"], code, side="right")
            if b - a < 30:
                continue
            bars = tuple(A[k][a:b] for k in ("m", "o", "h", "l", "c"))
            v = want[(s, d)]

            def run(side, fill="next"):
                t = simulate(*bars, side, fill)
                if t is None:
                    return None
                when = A["ts"].iloc[a + t["i"]].tz_localize("Asia/Kolkata")
                return dict(t, when=when, sym=s, kind=kinds[d])
            for variant in ("shipped", "documented"):
                if variant in v:
                    t = run(v[variant])
                    if t:
                        rows[variant].append(t)
            if "shipped" in v:
                t = run(v["shipped"], "close")
                if t:
                    rows["paper"].append(t)
                t = run("LONG" if v["shipped"] == "SHORT" else "SHORT")
                if t:
                    rows["mirror"].append(t)
                for n, heads in enumerate(coins[(s, d)]):
                    t = run("LONG" if heads else "SHORT")
                    if t:
                        coin_rows[n].append(t)
    s_ = lambda rs: sorted(rs, key=lambda r: r["when"])
    B = {k: s_([book(t) for t in v]) for k, v in rows.items()}
    B["paper"] = s_([book(t, 0.0) for t in rows["paper"]])
    B["stress"] = s_([book(t, 0.0005) for t in rows["shipped"]])
    C = [s_([book(t) for t in cr]) for cr in coin_rows]
    tot = lambda rs, oos: sum(r["net"] for r in rs if (r["when"] >= sds.SPLIT) == oos)
    print("#" * 120)
    print("3-MINUTE BREAKOUT (arshadakl), Nifty 50 stocks, per Rs 1 lakh a trade after charges  (to 15 Aug 2025 | held out)")
    for name, key in (("AS SHIPPED", "shipped"), ("AS DOCUMENTED (no 1% stock-gap filter)", "documented"),
                      ("as shipped, 0.05% slippage", "stress"), ("its own paper trading (candle-close fill)", "paper"),
                      ("MIRROR (the other side, reference only)", "mirror")):
        print(sds.line(f"   {name}", B[key]))
    for oos, label in ((False, "to 15 Aug 2025"), (True, "held out")):
        ct = sorted(tot(c, oos) for c in C)
        print(f"   COIN x20 ({label}): mean {np.mean(ct):+,.0f}, from {ct[0]:+,.0f} to {ct[-1]:+,.0f}; "
              f"as shipped {tot(B['shipped'], oos):+,.0f} beats {sum(tot(B['shipped'], oos) > x for x in ct)} of 20")
    zero = lambda rs: [dict(r, net=0.0) for r in rs]
    for name, key in (("as shipped vs doing nothing", "shipped"), ("as documented vs doing nothing", "documented")):
        text = sds.verdict(name, B[key], zero(B[key])).strip()
        print("   " + text.replace("\n", "\n   "))
    print("=" * 120)
    for key in ("shipped", "documented"):
        df = pd.DataFrame(B[key])
        print(f"{key.upper()}: {len(df)} trades on {df['when'].dt.date.nunique()} days; won {100 * (df['net'] > 0).mean():.0f}%, "
              f"avg {df['pct'].mean():+.3f}% a trade (gross {100 * (df['gross'] / NOTIONAL).mean():+.3f}%); exits "
              + ", ".join(f"{k} {100 * v:.0f}%" for k, v in df["how"].value_counts(normalize=True).items())
              + f"; stop and target in one minute {int(df['both'].sum())}; wide reference candle {100 * df['large'].mean():.0f}%")
        for k, g in df.groupby("kind"):
            o = g["when"] >= sds.SPLIT
            print(f"   {k:9s} {len(g):5d} trades  won {100 * (g['net'] > 0).mean():3.0f}%  Rs {g['net'].sum():+10,.0f}"
                  f"   (to 15 Aug 2025 {g.loc[~o, 'net'].sum():+,.0f} | held out {g.loc[o, 'net'].sum():+,.0f})")
        for y, g in df.groupby(df["when"].dt.year):
            print(f"   {y}      {len(g):5d} trades  won {100 * (g['net'] > 0).mean():3.0f}%  Rs {g['net'].sum():+10,.0f}")


if __name__ == "__main__":
    main()
