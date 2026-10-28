#!/usr/bin/env python3
"""
entry_decay_check.py — do the tickets buy at the top of the option's price, and does decay eat their targets?
================================================================================
The user, 28 Oct 2026: "tool is giving the targets but it not looking at the option price decay or support
resistance and enter in the highest option price". Measured on the recorder's real option prices (10 Sep - 26 Oct),
on the trades of the TradingView rating and the rules (real_price_check.py's, nearest expiry, at the money):
  ENTRY      where the price paid (the entry minute's open) sat in the option's own range over the 15 minutes before
             (0% = the low, 100% = the high), and how far it fell in the 15 minutes after (the dip a buyer waiting
             could have had)
  DECAY      what the option really made against what the ticket's premium levels assume - the index's move x 0.5
             (config.APPROX_ATM_DELTA) - per lot: the gap is decay (and volatility moves), by days to expiry
  TARGETS    on trades whose index reached a target level: was the option at its premium target (price paid + the
             index's distance x 0.5) at that minute?

RESULT (28 Oct 2026; entry_decay_check_results.txt), 297 trades:
  ENTRY: bought at a median 74% of the option's last-15-minute range (the rating 78%, 87% of its trades in the top half),
  13% above that range's low; it then dipped a median 6.7% within 15 minutes (5%+ in 59% of trades). BUT the top-20%
  buys made the most (+25 a trade) and the bottom-half buys the least (-465): an option bought falling kept falling.
  A limit order below the price paid, good 15 minutes: 2% lower -68,033, 3% -67,626, 5% -75,147, 8% -19,055 against
  -54,519 paying the price - it misses the winners (+72,654 to +116,018 at the market), which do not dip; not a rule.
  DECAY: the option made +16 a trade MORE than the index's move x 0.5 says (the premium levels' assumption) -
  decay is about paid for by the gain on fast moves; only 1-3 days from expiry did it fall short (-117 a trade).
  TARGETS: one of the rating's trades reached its target in these weeks - nothing to judge.

    python3 entry_decay_check.py OPTION_HISTORY_DIR      (HOME at the six-year history)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
IST = "Asia/Kolkata"
LIMITS = (0.02, 0.03, 0.05, 0.08)       # how far below the price paid the limit order sits
KEYS = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY")


def main():
    import multiprocessing as mp
    import config
    import live_vs_backtest as lv
    import real_price_check as rpc
    P = lv.Prices(sys.argv[1])
    jobs = [(k, s) for k in KEYS for s in ("TV", "RULES")]
    with mp.get_context("spawn").Pool(processes=6, maxtasksperchild=1) as pool:
        got = dict(pool.map(rpc._job, jobs, chunksize=1))
    out = []
    for (k, system), rows in got.items():
        meta = config.INSTRUMENTS[k]
        qty, step = meta["lot_size"], meta["strike_step"]
        for r in rows:
            t_in, t_out = pd.Timestamp(r["when"]), pd.Timestamp(r["exit_time"])
            if not (rpc.SINCE <= t_in < rpc.UNTIL):
                continue
            d = t_in.date().isoformat()
            exps = [e for e in P.expiries(k, d) if e >= d]
            if not exps:
                continue
            K = round(r["entry_spot"] / step) * step
            opt = P.option(k, d, exps[0], K, r["side"])
            if opt is None:
                continue
            p0 = lv.at_open(opt, t_in)
            before = opt[(opt.index >= t_in - pd.Timedelta(minutes=15)) & (opt.index < t_in)]
            after = opt[(opt.index >= t_in) & (opt.index < t_in + pd.Timedelta(minutes=15))]
            if p0 is None or p0 <= 0.5 or len(before) < 5 or len(after) < 5:
                continue
            lo, hi = before["low"].min(), before["high"].max()
            ce = r["side"] == "CE"
            rr = dict(r, index=k, system=system)
            real = rpc.real_net(rr, P, 0)
            if real is None:
                continue
            # the option's gross move vs the index move x 0.5 (what the ticket's premium levels assume)
            idx_move = (r["exit_spot"] - r["entry_spot"]) * (1 if ce else -1)
            # gross option move from net: undo charges roughly by re-pricing with no slippage/costs
            import regime_study as rs
            exch = "BSE" if meta["kite_exchange"] == "BSE" else "NSE"
            costs = rs.net_rupees(p0, p0, qty, exch, 0.0025)            # a round trip at the same price: the costs
            gross = real[0] - costs
            days_left = real[1]
            # a limit order below the price paid, good for 15 minutes or until the trade's own exit: filled at the limit
            # when the option's low reaches it; the exit unchanged (the same minute and price)
            p1 = p0 + gross / qty
            win_end = min(t_in + pd.Timedelta(minutes=15), t_out)
            low_in = opt[(opt.index >= t_in) & (opt.index < win_end)]["low"].min()
            limits = {}
            for x in LIMITS:
                lim = p0 * (1 - x)
                limits[x] = rs.net_rupees(lim, p1, qty, exch, 0.0025) if low_in == low_in and low_in <= lim else None
            row = {"index": k, "system": system, "pos": (p0 - lo) / (hi - lo) if hi > lo else 0.5, "limits": limits,
                   "dip": (p0 - after["low"].min()) / p0, "runup_before": (p0 - lo) / p0,
                   "gross": gross, "delta_view": 0.5 * idx_move * qty, "dte": days_left, "p0": p0,
                   "net": real[0]}
            # TARGETS (the TradingView rating's: its walk says when the index reached the target): the option at that
            # minute against the premium target a ticket sets - the price paid + the index's distance x 0.5
            if system == "TV" and r.get("closed_via") == "target":
                row["prem_target"] = p0 + 0.5 * abs(r["exit_spot"] - r["entry_spot"])
                row["prem_at_target"] = p0 + gross / qty
            out.append(row)
    d = pd.DataFrame(out)
    print(f"{len(d)} trades priced on the recorder's real option prices, 10 Sep - 26 Oct "
          f"(TradingView rating {sum(d.system == 'TV')}, rules {sum(d.system == 'RULES')})")
    print()
    print("1. ENTRY - where the price paid sat in the option's own last-15-minute range")
    for name, g in (("both", d),) + tuple((s, d[d.system == s]) for s in ("TV", "RULES")):
        print(f"   {name:6s} median {100 * g.pos.median():3.0f}% of the range; at or above 80% in "
              f"{100 * (g.pos >= 0.8).mean():3.0f}% of trades, in the top half in {100 * (g.pos >= 0.5).mean():3.0f}%; "
              f"risen {100 * g.runup_before.median():.1f}% from that 15-minute low (median)")
    print(f"   after entry: the option dipped a median {100 * d.dip.median():.1f}% within 15 minutes; 5%+ in "
          f"{100 * (d.dip >= 0.05).mean():.0f}% of trades, 10%+ in {100 * (d.dip >= 0.10).mean():.0f}%")
    by = d.assign(q=pd.cut(d.pos, [-0.01, 0.5, 0.8, 1.01], labels=["bottom half", "50-80%", "top 20%"]))
    print("   result by where it was bought (net per lot):")
    print(by.groupby("q", observed=True)["net"].agg(["count", "mean", "sum"]).round(0).to_string())
    print()
    print("2. DECAY - what the option made against the index move x 0.5 (what the premium levels assume), per lot")
    d["gap"] = d.gross - d.delta_view
    d["dte_b"] = pd.cut(d.dte, [0, 1, 3, 7, 400], labels=["under 1 day", "1-3 days", "3-7 days", "7+ days"])
    print(d.groupby("dte_b", observed=True).agg(trades=("gap", "count"), option_made=("gross", "mean"),
                                                 index_x_half=("delta_view", "mean"), gap=("gap", "mean"))
          .round(0).to_string())
    print(f"   all: the option made {d.gross.mean():+,.0f} a trade where index x 0.5 says {d.delta_view.mean():+,.0f} - "
          f"{d.gap.mean():+,.0f} a trade lost to decay and volatility")
    print()
    print()
    print("4. A LIMIT ORDER BELOW THE PRICE PAID, good for 15 minutes (or to the trade's own exit) - per lot, net")
    print(f"   paying the price (today):  {len(d):3d} trades  {d.net.sum():+10,.0f}  ({d.net.mean():+,.0f} a trade)")
    for x in LIMITS:
        got = [r[x] for r in d.limits if r[x] is not None]
        missed = d[[r[x] is None for r in d.limits]]
        print(f"   limit {100 * x:2.0f}% lower:  filled {len(got):3d} ({100 * len(got) / len(d):3.0f}%)  "
              f"{sum(got):+10,.0f}   | the trades it missed made {missed.net.sum():+,.0f} at the market")
    for sname in ("TV", "RULES"):
        g = d[d.system == sname]
        print(f"   {sname:6s} today {g.net.sum():+9,.0f}   limit 5%: "
              f"{sum(r[0.05] for r in g.limits if r[0.05] is not None):+9,.0f} "
              f"(filled {sum(r[0.05] is not None for r in g.limits)} of {len(g)})")
    t = d.dropna(subset=["prem_target"]) if "prem_target" in d else d.iloc[:0]
    print(f"3. TARGETS - the TradingView rating's trades whose index reached its target ({len(t)}):")
    if len(t):
        short = (t.prem_target - t.prem_at_target) / t.prem_target
        print(f"   the option was BELOW its premium target in {100 * (short > 0).mean():.0f}% of them, by a median "
              f"{100 * short.median():.1f}% - a ticket tracked on the premium would still be waiting")


if __name__ == "__main__":
    main()
