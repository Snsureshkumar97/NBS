#!/usr/bin/env python3
"""
stock_votes_study.py — the index's own stocks, and the tool's votes run on each of them, as a filter on today's trades
================================================================================
The user, 27 Oct 2026, after the Market X-Ray put the index's stocks beside the votes: "what if we filter through those
stock for more stocks going up or down based on those if we calculate our 7 votes and take the trade we can bastest and
check this".

Breadth was tested on 14 Oct (breadth_study.py: the share of the index's stocks up or down on the day, five versions,
all DROP) - but against the rules as they ran then, and only on each stock's price. Two things are new here: the base is
TODAY's system (the rules with the big candle in them, as live since 27 Oct), and the tool's own votes are run on every
stock - the same four (Trend: close and EMA 20 against EMA 50; MACD histogram; RSI 50-75 / 25-50; close against VWAP), the
same thresholds (backtest_intraday.tech_at), on each stock's own 15-minute candles.

DATA: 15-minute candles of the 56 market-map stocks (market_map.CONSTITUENTS: Nifty 49, Bank Nifty 12, Sensex 29), Sep
2023 - Oct 2026 (~/trading-tool-logs/history/stocks), against the indices' cached three years (18 Sep 2023 - 11 Sep
2026). TODAY's member lists for all three years (survivorship - the same for every version). Midcap Select has no member
list and is not tested.

READ AT A SIGNAL CANDLE'S CLOSE, only what was known then - for each stock, in the same 15-minute candle:
  day move   its close against its previous day's close: up / down
  lean       its own four votes: more for than against = up, more against = down (the index's own count)
  signal     the index's own rule on the stock: 3 or more of the 4 agree, at most 1 against, its ADX 20 or more
Shares are of the stocks with a candle there, equal weight, or by market_map's index weights (W).

FIXED BEFORE ANY RESULT WAS SEEN (27 Oct 2026). BASE is today's system: order_block_study.rules_rows (the rules as live
runs them - live room, out at T3 with the stop stepped to T1 / T2, the Supertrend trail after T1, the T1-exit re-entry,
the conditional cooldown) plus big_candle_study's live version (K1.5, stop at the candle's end, 2R) where the rules hold
nothing (order_block_site_study.slot) - at the money on the real expiry, 0.25% slippage, every charge, per lot. Each
version adds ONE condition to every entry, the rules' and the big candle's (a blocked rules signal frees the slot as it
would live):
  MORE     more of the index's stocks up on the day than down for a CE; more down than up for a PE
  WMORE    MORE by index weight (the heavyweights count more)
  VLEAN    more stocks whose own votes lean the trade's way than lean the other way
  WVLEAN   VLEAN by index weight
  VSIG     more stocks firing their own signal the trade's way than the other way (none either way blocks)
A candle with no stock data blocks nothing.
VERDICT (stop_day_study.verdict): kept only if more than BASE in BOTH the first two years (to 15 Aug 2025) and the
held-out year, Nifty + Bank Nifty + Sensex pooled (all three run the rules), and then on each index on its own; the
walk-forward half-years and the Monte Carlo drawdown are printed under it. Five versions are tried, so one passing by luck
is possible - a pass that does not hold on each index is read as luck.

RESULT (27 Oct 2026; stock_votes_study_results.txt) - ALL FIVE DROP, NOTHING BUILT. All three pooled, per lot, first two
years / held-out, against BASE +529,169 / +186,262:
  MORE      -86,272 / -103,120        WMORE     +8,044 / -77,994       VLEAN    -37,366 / -70,975
  WVLEAN       +886 /  -62,817        VSIG     -96,979 / -52,437
No version beats today's in the held-out year, pooled or on any index but one (VSIG on Bank Nifty, +6,611, while it
loses -46,114 there in the first two years); the weighted ones' first-period gains (+8k, +1k) are under 2% and gone
held-out. Why, as with breadth on 14 Oct: the trades taken with only 30-50% of the stocks' own votes leaning their way
still make money (PF 1.11-1.88 in every period on every index), so blocking them throws away winners; the trades with
70%+ agreeing are the best group on Sensex in both periods (PF 1.67 / 1.59) but not on Nifty held-out (1.15) or Bank
Nifty (1.12 / 1.02, its weakest group) - a pattern seen after the fact on one index, not a rule.

    /usr/bin/python3 stock_votes_study.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
VARIANTS = ("BASE", "MORE", "WMORE", "VLEAN", "WVLEAN", "VSIG")
STOCKS = os.path.expanduser("~/trading-tool-logs/history/stocks")
BAR = pd.Timedelta(minutes=15)


def stock_reads(df, key):
    """Per candle of the index's df: the shares of its stocks up / down on the day, leaning up / down on their own votes,
    and firing their own signal up / down - equal weight and by index weight - and how many had a candle there."""
    import config
    import indicators as ind
    import market_map
    members = market_map.CONSTITUENTS[key]
    day_mv, lean, sig, wts = {}, {}, {}, {}
    for sym, _, wt in members:
        s = pd.read_csv(os.path.join(STOCKS, f"{sym}_15m.csv"), index_col=0, parse_dates=True)
        c = s["Close"]
        day = pd.Series(s.index.date, index=s.index)
        prev = day.map(c.groupby(day.values).last().shift(1))
        es, ef = ind.ema(c, config.EMA_SLOW), ind.ema(c, config.EMA_FAST)
        r = ind.rsi(c, config.RSI_LENGTH)
        hist = ind.macd(c, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)[2]
        v = ind.vwap(s)
        a = ind.adx(s, config.ADX_LENGTH, config.adx_dx_smoothing(key))
        votes = [np.where((c > es) & (ef > es), 1, np.where((c < es) & (ef < es), -1, 0)),
                 np.sign(hist.fillna(0).to_numpy()),
                 np.where((r > config.RSI_BULL_MIN) & (r < config.RSI_OVERBOUGHT), 1,
                          np.where((r > config.RSI_OVERSOLD) & (r < config.RSI_BEAR_MAX), -1, 0)),
                 np.sign((c - v).fillna(0).to_numpy())]
        bulls = sum((x > 0).astype(int) for x in votes)
        bears = sum((x < 0).astype(int) for x in votes)
        trend_ok = (a >= config.ADX_TREND_THRESHOLD).to_numpy()
        ok_c = c.notna() & prev.notna()
        day_mv[sym] = pd.Series(np.where(ok_c, np.sign((c - prev).fillna(0)), np.nan), index=s.index).reindex(df.index)
        lean[sym] = pd.Series(np.where(bulls > bears, 1, np.where(bears > bulls, -1, 0)), index=s.index).reindex(df.index)
        sig[sym] = pd.Series(np.where((bulls >= 3) & (bears <= 1) & trend_ok, 1,
                                      np.where((bears >= 3) & (bulls <= 1) & trend_ok, -1, 0)), index=s.index).reindex(df.index)
        wts[sym] = wt
    W = pd.Series(wts)
    out = {}
    for name, frame in (("day", pd.DataFrame(day_mv)), ("lean", pd.DataFrame(lean)), ("sig", pd.DataFrame(sig))):
        ok = frame.notna()
        n = ok.sum(axis=1).replace(0, np.nan)
        wn = (ok * W).sum(axis=1).replace(0, np.nan)
        up, dn = (frame > 0), (frame < 0)
        out[name] = {"up": (up.sum(axis=1) / n).to_numpy(), "dn": (dn.sum(axis=1) / n).to_numpy(),
                     "wup": ((up * W).sum(axis=1) / wn).to_numpy(), "wdn": ((dn * W).sum(axis=1) / wn).to_numpy()}
    out["cover"] = pd.DataFrame(day_mv).notna().sum(axis=1).to_numpy()
    return out


def allowed(variant, R, i, ce):
    """Whether the version lets a CE (ce=True) or a PE through at candle i. No stock data there blocks nothing."""
    if variant == "BASE" or R["cover"][i] == 0:
        return True
    src, u, d = {"MORE": ("day", "up", "dn"), "WMORE": ("day", "wup", "wdn"), "VLEAN": ("lean", "up", "dn"),
                 "WVLEAN": ("lean", "wup", "wdn"), "VSIG": ("sig", "up", "dn")}[variant]
    a, b = R[src][u][i], R[src][d][i]
    if not (a == a and b == b):
        return True
    return a > b if ce else b > a


def agree(R, i, ce):
    """The share of the stocks whose own votes lean the trade's way, for the table of what BASE's trades earned."""
    return R["lean"]["up" if ce else "dn"][i]


def _rules_job(job):
    key, v = job
    import order_block_study as ob

    def make(df, pre):
        R = stock_reads(df, key)
        note = lambda i, p: p.update({"agree": agree(R, i, p["side"] == "CE"), "cover": int(R["cover"][i])})
        return (None if v == "BASE" else (lambda i, buy: allowed(v, R, i, buy))), None, note
    return job, ob.rules_rows(key, make=make)


def _bc_job(key):
    """The big candle's live version, then each version's own cut of it - read at the big candle's own close."""
    import backtest_intraday as bt
    import big_candle_study as bcs
    rows = bcs.run(key, ("K1.5 end 2R", 1.5, True, "end", "2R"))
    df = bt.fetch_history(key, years=3, use_cache=True)
    R = stock_reads(df, key)
    pos = {t: n for n, t in enumerate(df.index)}
    out = {}
    for v in VARIANTS:
        keep = []
        for r in rows:
            i = pos.get(pd.Timestamp(r["when"]) - BAR)
            if i is None or allowed(v, R, i, r["side"] == "CE"):
                keep.append(r if i is None else dict(r, agree=agree(R, i, r["side"] == "CE")))
        out[v] = keep
    return key, out


def main():
    import multiprocessing as mp
    import order_block_site_study as obs
    import stop_day_study as sds
    jobs = [(k, v) for k in INDICES for v in VARIANTS]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        rules = dict(pool.map(_rules_job, jobs, chunksize=1))
        bc = dict(pool.map(_bc_job, list(INDICES), chunksize=1))
    s_ = lambda rows: sorted(rows, key=lambda r: pd.Timestamp(r["when"]))
    got = {(k, v): obs.slot(s_(rules[(k, v)]), s_(bc[k][v])) for k in INDICES for v in VARIANTS}
    pool_ = lambda v: s_([r for k in INDICES for r in got[(k, v)]])
    W = 172
    print("=" * W)
    print(" THE INDEX'S OWN STOCKS AS A FILTER ON TODAY'S SYSTEM (the rules + the big candle, 15-minute). Per lot, after costs."
          "  columns: trades, won, total, profit factor, worst drop, worst single DAY")
    print("=" * W)
    print(f" {'':46s} {'FIRST TWO YEARS (to 15 Aug 2025)':66s}  | HELD-OUT FINAL YEAR")
    print(" NIFTY + BANK NIFTY + SENSEX (the verdict)")
    for v in VARIANTS:
        print(sds.line("   " + v, pool_(v)))
    for k in INDICES:
        print(f" {k}")
        for v in VARIANTS:
            print(sds.line("   " + v, got[(k, v)]))
    print("\n HOW OFTEN THE STOCKS' OWN VOTES ALREADY AGREE with BASE's trades (the share of the index's stocks leaning the"
          " trade's way), and what each group earned")
    for k in INDICES:
        base = got[(k, "BASE")]
        print(f" {k}: {len(base)} trades")
        for name, lo, hi in (("under 30% lean its way", 0.0, 0.3), ("30-50%", 0.3, 0.5), ("50-70%", 0.5, 0.7),
                             ("70%+ lean its way", 0.7, 1.01)):
            g = [r for r in base if r.get("agree") == r.get("agree") and r.get("agree") is not None and lo <= r["agree"] < hi]
            print(sds.line(f"   {name}", g) if g else f"   {name:44s} none")
    print("\n VERDICT - kept only if more than BASE in BOTH periods (all three pooled), and then on each index too")
    for v in VARIANTS[1:]:
        print("  " + sds.verdict(v, pool_(v), pool_("BASE")).strip())
        for k in INDICES:
            print("      " + sds.verdict(f"{v} {k}", got[(k, v)], got[(k, "BASE")]).strip())
    return got


if __name__ == "__main__":
    main()
